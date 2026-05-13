import base64
import json
import math
from datetime import date, datetime
from pytz import timezone, utc

from odoo import fields, http, _
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request
import logging
_logger = logging.getLogger(__name__)

def _localize_dt(dt, tz_name=None):
    """Convert naive UTC datetime → localized string for display. Fix #8."""
    if not dt:
        return ''
    if isinstance(dt, str):
        try:
            dt = datetime.strptime(dt, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return dt
    # dt is naive UTC (Odoo convention)
    dt_utc = utc.localize(dt)
    if tz_name:
        try:
            local_tz = timezone(tz_name)
            dt_local = dt_utc.astimezone(local_tz)
            return dt_local.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            pass
    return dt_utc.strftime('%Y-%m-%d %H:%M:%S')


class SalespersonTrackingController(http.Controller):

    def _check_access(self):
        user = request.env.user
        if user._is_public():
            raise AccessError(_("Please log in to access tracking."))
        if not hasattr(user, 'salesperson_role') or user.salesperson_role not in ("manager", "salesman"):
            raise AccessError(_("Only salespersons and managers can access tracking."))
        
        # Ensure the user has the tracker method
        if not hasattr(user, '_ensure_salesperson_tracker'):
            # Monkey patch if needed (better to add to res.users via inheritance)
            pass
        
        return user
    def _user_tz(self, user):
        """Return user's timezone name, falling back to UTC."""
        try:
            return user.tz or 'UTC'
        except Exception:
            return 'UTC'

    def _json_body(self):
        payload = request.httprequest.data or b"{}"
        return json.loads(payload.decode("utf-8"))

    @staticmethod
    def _haversine_km(lat1, lon1, lat2, lon2):
        R = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _compute_total_distance(self, logs):
        total, prev = 0.0, None
        for log in logs:
            if not (log.latitude and log.longitude):
                continue
            if prev:
                total += self._haversine_km(prev[0], prev[1], log.latitude, log.longitude)
            prev = (log.latitude, log.longitude)
        return round(total, 3)

    def _json_response(self, data):
        """Return a JSON HTTP response compatible with Odoo 19."""
        return request.make_response(
            json.dumps(data),
            headers=[('Content-Type', 'application/json')],
        )

    # ── Pages ─────────────────────────────────────────────────────────────────

    @http.route("/salesperson_tracking/live", type="http", auth="user", website=False)
    def live_tracking_page(self, tracker_id=None, **kwargs):
        user = self._check_access()

        if tracker_id:
            tracker = request.env["salesperson.tracker"].sudo().browse(int(tracker_id))
            if not tracker.exists():
                return request.not_found()
        else:
            # No tracker_id in URL — always the user's own page
            tracker = user._ensure_salesperson_tracker()

        today = fields.Date.today()
        today_start = fields.Datetime.to_datetime(today)

        logs = request.env["salesperson.location.log"].sudo().search([
            ("tracker_id", "=", tracker.id),
            ("tracked_at", ">=", today_start),
        ], order="tracked_at asc")
        total_distance = self._compute_total_distance(logs)

        # Fix #8: Localize last_seen for display
        tz_name = self._user_tz(user)
        last_seen_display = _localize_dt(tracker.last_seen, tz_name) if tracker.last_seen else 'Not tracked'

        values = {
            "tracker": tracker,
            "user": user,
            "total_distance_km": total_distance,
            "today_plan_count": tracker.total_visits,
            "today_visited_count": tracker.total_visited,
            "today_rate": int(tracker.total_visited * 100 / tracker.total_visits) if tracker.total_visits else 0,
            "last_seen_display": last_seen_display,
            # is_owner: True  → show Start/Stop buttons (this is YOUR tracker)
            #           False → show read-only badge (viewing someone else's tracker)
            # Compare raw integer IDs — sudo() on tracker does not change field values.
            "is_owner": int(tracker.user_id.id) == int(user.id),
        }
        _logger.info(
            "live_tracking_page: uid=%s(%s) tracker=%s tracker_uid=%s(%s) is_owner=%s url_tracker_id=%s",
            user.id, user.name,
            tracker.id,
            tracker.user_id.id, tracker.user_id.name,
            values["is_owner"], tracker_id,
        )
        return request.render("zencore_salesperson_tracking.live_tracking_page", values)

    @http.route("/salesperson_tracking/moving_map/<int:tracker_id>", type="http", auth="user", website=False)
    def moving_map(self, tracker_id, **kwargs):
        user = self._check_access()
        _logger.info("moving_map: request tracker_id=%s by user=%s (id=%s)", tracker_id, user.name, user.id)

        tracker = request.env["salesperson.tracker"].sudo().browse(tracker_id)
        if not tracker.exists():
            _logger.warning("moving_map: tracker_id=%s does not exist", tracker_id)
            return request.not_found()

        _logger.info(
            "moving_map: tracker found — user=%s is_tracking=%s last_seen=%s last_lat=%s last_lng=%s",
            tracker.user_id.name, tracker.is_tracking, tracker.last_seen,
            tracker.last_latitude, tracker.last_longitude,
        )

        today = fields.Date.today()
        today_start = fields.Datetime.to_datetime(today)
        tracking_status = tracker.tracking_status or 'offline'
        tz_name = self._user_tz(user)

        logs = request.env["salesperson.location.log"].sudo().search([
            ("tracker_id", "=", tracker_id),
            ("tracked_at", ">=", today_start),
        ], order="tracked_at asc")

        _logger.info(
            "moving_map: found %d location logs today for tracker_id=%s (since %s)",
            len(logs), tracker_id, today_start,
        )

        points = []
        skipped = 0
        for log in logs:
            if log.latitude and log.longitude:
                points.append({
                    "lat": log.latitude,
                    "lng": log.longitude,
                    "accuracy": log.accuracy,
                    "time": _localize_dt(log.tracked_at, tz_name),
                    "location_name": log.location_name or "",
                })
            else:
                skipped += 1

        if skipped:
            _logger.warning(
                "moving_map: skipped %d logs with missing coordinates for tracker_id=%s",
                skipped, tracker_id,
            )

        _logger.info("moving_map: %d valid GPS points for tracker_id=%s", len(points), tracker_id)

        # Fallback: if no log points exist yet, show last known position
        if not points and tracker.last_latitude and tracker.last_longitude:
            _logger.info(
                "moving_map: no log points — using last_known fallback lat=%s lng=%s",
                tracker.last_latitude, tracker.last_longitude,
            )
            points.append({
                "lat": tracker.last_latitude,
                "lng": tracker.last_longitude,
                "accuracy": tracker.last_accuracy,
                "time": _localize_dt(tracker.last_seen, tz_name),
                "location_name": tracker.location_name or "Last known position",
            })
        elif not points:
            _logger.warning(
                "moving_map: NO points available for tracker_id=%s — tracker has no last_latitude either",
                tracker_id,
            )

        points_b64 = base64.b64encode(json.dumps(points).encode()).decode()

        # ── Customer pins ──────────────────────────────────────────────────
        # Sources (in priority order):
        #   1. tracker.target_line_ids  — rows loaded by action_load_target_lines
        #   2. tracker.plan_id.target_line_ids — raw plan lines (fallback)
        #   3. tracker.visited_line_ids — visited customers not already in the plan
        # We deduplicate by partner.id to avoid double-pins.
        # Status: visited > not-yet-visited (planned) > skipped (planned but not visited and not remaining)
        customers = []
        seen_partner_ids = set()
        visited_ids   = {v.partner_id.id for v in tracker.visited_line_ids}
        _logger.info(
            "moving_map: building customer pins tracker=%s plan_id=%s visited_ids=%s",
            tracker_id, tracker.plan_id.id if tracker.plan_id else None, visited_ids,
        )

        def _add_partner(partner, override_visited=None):
            if not partner or partner.id in seen_partner_ids:
                return
            lat = partner.partner_latitude
            lng = partner.partner_longitude
            if not lat or not lng:
                _logger.debug(
                    "moving_map: partner %r (id=%s) has no coordinates — skipped",
                    partner.name, partner.id,
                )
                return
            visited = override_visited if override_visited is not None else (partner.id in visited_ids)
            customers.append({
                "id":      partner.id,
                "name":    partner.name,
                "lat":     lat,
                "lng":     lng,
                "visited": visited,
            })
            seen_partner_ids.add(partner.id)
            _logger.debug(
                "moving_map: customer pin added — name=%r id=%s lat=%.6f lng=%.6f visited=%s",
                partner.name, partner.id, lat, lng, visited,
            )

        # Source 1: tracker.target_line_ids (most reliable — loaded for this tracker)
        for tline in tracker.target_line_ids:
            for partner in tline.customer_ids:
                _add_partner(partner)

        # Source 2: fallback to plan lines if target_line_ids is empty
        if not seen_partner_ids and tracker.plan_id:
            _logger.info(
                "moving_map: no target_line_ids on tracker — falling back to plan_id=%s lines",
                tracker.plan_id.id,
            )
            for pline in tracker.plan_id.target_line_ids:
                for partner in pline.customer_ids:
                    _add_partner(partner)

        # Source 3: visited customers not already captured
        for vline in tracker.visited_line_ids:
            _add_partner(vline.partner_id, override_visited=True)

        _logger.info(
            "moving_map: %d customer pins for tracker_id=%s (visited=%d, pending=%d)",
            len(customers), tracker_id,
            sum(1 for c in customers if c["visited"]),
            sum(1 for c in customers if not c["visited"]),
        )

        customers_b64 = base64.b64encode(json.dumps(customers).encode()).decode()
        last_seen_display_map = _localize_dt(tracker.last_seen, tz_name) if tracker.last_seen else '—'

        values = {
            "tracker": tracker,
            "points_b64": points_b64,
            "customers_b64": customers_b64,
            "total_logs": len(points),
            "tracking_status": tracking_status,
            "tracker_id": tracker_id,
            "last_seen_display": last_seen_display_map,
            "last_known_lat": tracker.last_latitude or 0,
            "last_known_lng": tracker.last_longitude or 0,
        }
        return request.render("zencore_salesperson_tracking.moving_map_page", values)

    # ── Dashboard ─────────────────────────────────────────────────────────────

    @http.route("/salesperson/dashboard", type="http", auth="user", website=False)
    def salesperson_dashboard(self, **kwargs):
        """Fix #5: Dashboard page."""
        user = self._check_access()
        tz_name = self._user_tz(user)
        is_manager = getattr(user, 'salesperson_role', '') == 'manager'

        today = fields.Date.today()
        today_start = fields.Datetime.to_datetime(today)

        # All trackers today
        Tracker = request.env["salesperson.tracker"].sudo()
        all_trackers = Tracker.search([("visit_date", "=", today)])

        rows = []
        live_count = offline_count = 0
        total_planned = total_covered = 0
        deviation_alerts = 0

        COLORS = [
            'linear-gradient(135deg,#2563EB,#4F46E5)',
            'linear-gradient(135deg,#059669,#10B981)',
            'linear-gradient(135deg,#D97706,#F59E0B)',
            'linear-gradient(135deg,#DC2626,#E11D48)',
            'linear-gradient(135deg,#7C3AED,#A78BFA)',
        ]

        for i, t in enumerate(all_trackers):
            status = t.tracking_status or 'offline'
            if status == 'live':   live_count += 1
            # idle removed — is_tracking=True is always 'live'
            else:                  offline_count += 1

            total_planned += t.total_visits
            total_covered += t.total_visited

            name = t.user_id.name or 'Unknown'
            initials = ''.join(p[0].upper() for p in name.split()[:2])

            # Fix #8: localize last_seen
            last_seen_str = '—'
            if t.last_seen:
                last_seen_str = _localize_dt(t.last_seen, tz_name)

            rows.append({
                'id':       t.id,
                'name':     name,
                'team':     t.sale_team_id.name if t.sale_team_id else '—',
                'initials': initials,
                'status':   status,
                'state':    t.state or 'planned',
                'covered':  t.total_visited,
                'total':    t.total_visits,
                'pct':      int(t.total_visited * 100 / t.total_visits) if t.total_visits else 0,
                'distance': f"{t.total_distance_km:.1f} km",
                'last_seen': last_seen_str,
                'gradient': COLORS[i % len(COLORS)],
            })

        # Activity feed — recent location logs
        logs = request.env["salesperson.location.log"].sudo().search([
            ("tracked_at", ">=", today_start),
        ], order="tracked_at desc", limit=20)

        DOT_COLORS = {'live': '#059669', 'offline': '#94A3B8'}
        activity_feed = []
        for log in logs:
            status = log.tracker_id.tracking_status if log.tracker_id else 'offline'
            activity_feed.append({
                'name': log.tracker_id.user_id.name if log.tracker_id and log.tracker_id.user_id else '—',
                'desc': log.location_name or f"{log.latitude:.4f}, {log.longitude:.4f}",
                'time': _localize_dt(log.tracked_at, tz_name)[11:16] if log.tracked_at else '—',
                'dot_color': DOT_COLORS.get(status, '#94A3B8'),
            })

        # KPI
        kpi_pct = int(total_covered * 100 / total_planned) if total_planned else 0
        in_progress_count = sum(1 for r in rows if r['state'] == 'in_progress')
        missed_count = sum(1 for r in rows if r['pct'] == 0 and r['state'] not in ('planned',))

        # My tracker (salesperson)
        my_tracker = None
        my_status = 'offline'
        my_dist = '0.0'
        my_covered = 0
        my_total = 0
        my_location = '—'
        my_checkins_today = 0

        if not is_manager:
            my_tracker = Tracker.search([('user_id', '=', user.id), ('visit_date', '=', today)], limit=1)
            if my_tracker:
                my_status = my_tracker.tracking_status or 'offline'
                my_dist = f"{my_tracker.total_distance_km:.1f}"
                my_covered = my_tracker.total_visited
                my_total = my_tracker.total_visits
                my_location = my_tracker.location_name or '—'
                my_checkins_today = len(my_tracker.visited_line_ids)

        import locale as _locale
        try:
            today_label = today.strftime('%A, %d %B %Y')
        except Exception:
            today_label = str(today)

        values = {
            "user": user,
            "is_manager": is_manager,
            "today": today_label,
            "rows": rows,
            "live_count": live_count,
            "idle_count": 0,  # idle removed — was merged into live
            "offline_count": offline_count,
            "total_reps": len(rows),
            "total_planned": total_planned,
            "total_covered": total_covered,
            "deviation_alerts": deviation_alerts,
            "activity_feed": activity_feed,
            "kpi_pct": kpi_pct,
            "kpi_covered": total_covered,
            "kpi_in_progress": in_progress_count,
            "kpi_missed": missed_count,
            "my_tracker": my_tracker,
            "my_status": my_status,
            "my_dist": my_dist,
            "my_covered": my_covered,
            "my_total": my_total,
            "my_location": my_location,
            "my_checkins_today": my_checkins_today,
        }
        return request.render("zencore_salesperson_tracking.salesperson_dashboard", values)

    # ── Dashboard data API (for map AJAX) ─────────────────────────────────────

    @http.route("/salesperson/dashboard/data", type="http", auth="user", methods=["GET"], csrf=False)
    def dashboard_data(self, **kwargs):
        """JSON endpoint for live map markers."""
        user = self._check_access()
        today = fields.Date.today()
        Tracker = request.env["salesperson.tracker"].sudo()
        trackers = Tracker.search([("visit_date", "=", today)])

        result = []
        for t in trackers:
            status = t.tracking_status or 'offline'
            name = t.user_id.name or ''
            initials = ''.join(p[0].upper() for p in name.split()[:2])
            result.append({
                "id": t.id,
                "name": name,
                "initials": initials,
                "status": status,
                "lat": t.last_latitude or 0,
                "lng": t.last_longitude or 0,
                "location": t.location_name or '',
                "covered": t.total_visited,
                "total": t.total_visits,
            })

        return self._json_response({"ok": True, "trackers": result})

    # ── JSON API ──────────────────────────────────────────────────────────────

    @http.route("/salesperson_tracking/geocode", type="http", auth="user", methods=["POST"], csrf=False)
    def geocode_location(self, **kwargs):
        """Reverse-geocode a lat/lng and update the tracker location_name.
        Called by the JS every ~30 s — completely separate from the 2-second
        GPS log updates so geocoding never blocks log creation.
        """
        user = self._check_access()
        payload = self._json_body()
        try:
            lat = float(payload["latitude"])
            lng = float(payload["longitude"])
        except (KeyError, ValueError):
            return self._json_response({"ok": False})

        tracker_id = payload.get("tracker_id")
        if tracker_id:
            tracker = request.env["salesperson.tracker"].sudo().browse(int(tracker_id))
        else:
            tracker = user._ensure_salesperson_tracker()

        if not tracker or not tracker.exists():
            return self._json_response({"ok": False})

        name = tracker._reverse_geocode(lat, lng)
        if name:
            tracker.write({"location_name": name})
            _logger.debug("geocode_location: tracker=%s → %r", tracker.id, name)

        return self._json_response({"ok": True, "location_name": name or tracker.location_name or ""})

    @http.route("/salesperson_tracking/update", type="http", auth="user", methods=["POST"], csrf=False)
    def update_location(self, **kwargs):
        user = self._check_access()
        payload = self._json_body()

        try:
            latitude  = float(payload["latitude"])
            longitude = float(payload["longitude"])
        except (KeyError, ValueError) as e:
            _logger.warning("update_location: invalid payload from user=%s: %s | payload=%s", user.name, e, payload)
            return self._json_response({"ok": False, "error": "Latitude and longitude required."})

        tracker_id = payload.get("tracker_id")
        if tracker_id:
            tracker = request.env["salesperson.tracker"].sudo().browse(int(tracker_id))
            _logger.debug("update_location: using supplied tracker_id=%s for user=%s", tracker_id, user.name)
        else:
            tracker = user._ensure_salesperson_tracker()
            _logger.debug("update_location: auto-resolved tracker_id=%s for user=%s", tracker.id, user.name)

        if not tracker or not tracker.exists():
            _logger.error("update_location: no tracker found for user=%s tracker_id=%s", user.name, tracker_id)
            return self._json_response({"ok": False, "error": "No tracker found."})

        _logger.info(
            "update_location: user=%s tracker=%s lat=%.6f lng=%.6f accuracy=%s distance=%s",
            user.name, tracker.id, latitude, longitude,
            payload.get("accuracy"), payload.get("distance"),
        )

        try:
            tracker.update_live_location(
                latitude=latitude,
                longitude=longitude,
                accuracy=payload.get("accuracy"),
                source="browser",
                distance=float(payload.get("distance") or 0.0),
            )
        except Exception as e:
            _logger.error(
                "update_location: update_live_location failed for tracker=%s user=%s: %s",
                tracker.id, user.name, e, exc_info=True,
            )
            return self._json_response({
                "ok": False,
                "error": str(e),
                "tracker_id": tracker.id,
            })

        today_start = fields.Datetime.to_datetime(date.today())
        logs = request.env["salesperson.location.log"].sudo().search([
            ("tracker_id", "=", tracker.id),
            ("tracked_at", ">=", today_start),
        ], order="tracked_at asc")
        total_distance = self._compute_total_distance(logs)

        tz_name = self._user_tz(user)
        last_seen_local = _localize_dt(tracker.last_seen, tz_name)

        _logger.debug(
            "update_location: returning ok=True tracker=%s status=%s total_logs=%d distance=%.3f",
            tracker.id, tracker.tracking_status, len(logs), total_distance,
        )

        return self._json_response({
            "ok": True,
            "tracker_id": tracker.id,
            "status": tracker.tracking_status,
            "status_label": tracker.tracking_status_label,
            "last_seen": last_seen_local,
            "latitude": tracker.last_latitude,
            "longitude": tracker.last_longitude,
            "location_name": tracker.location_name or "",
            "map_url": tracker.openstreetmap_url or "",
            "total_distance_km": total_distance,
        })

    @http.route("/salesperson_tracking/moving_map_data/<int:tracker_id>", type="http", auth="user", methods=["GET"], csrf=False)
    def moving_map_data(self, tracker_id, **kwargs):
        """JSON endpoint: returns today's location points + current tracking status for live map polling."""
        user = self._check_access()
        tracker = request.env["salesperson.tracker"].sudo().browse(tracker_id)
        if not tracker.exists():
            _logger.warning("moving_map_data: tracker_id=%s not found", tracker_id)
            return self._json_response({"ok": False, "error": "Tracker not found"})

        today_start = fields.Datetime.to_datetime(fields.Date.today())
        logs = request.env["salesperson.location.log"].sudo().search([
            ("tracker_id", "=", tracker_id),
            ("tracked_at", ">=", today_start),
        ], order="tracked_at asc")

        tz_name = self._user_tz(user)
        points = []
        skipped = 0
        for log in logs:
            if log.latitude and log.longitude:
                points.append({
                    "lat": log.latitude,
                    "lng": log.longitude,
                    "time": _localize_dt(log.tracked_at, tz_name),
                    "accuracy": log.accuracy,
                    "location_name": log.location_name or "",
                })
            else:
                skipped += 1

        if skipped:
            _logger.debug(
                "moving_map_data: tracker_id=%s skipped %d logs with missing coords",
                tracker_id, skipped,
            )

        # Fallback: use last known position when no log points yet
        if not points and tracker.last_latitude and tracker.last_longitude:
            _logger.info(
                "moving_map_data: tracker_id=%s has no log points — using last_known fallback lat=%s lng=%s",
                tracker_id, tracker.last_latitude, tracker.last_longitude,
            )
            points.append({
                "lat": tracker.last_latitude,
                "lng": tracker.last_longitude,
                "time": _localize_dt(tracker.last_seen, tz_name) if tracker.last_seen else "",
                "accuracy": tracker.last_accuracy,
                "location_name": tracker.location_name or "",
            })

        total_dist = self._compute_total_distance(logs)

        _logger.debug(
            "moving_map_data: tracker_id=%s status=%s points=%d total_dist=%.3f",
            tracker_id, tracker.tracking_status, len(points), total_dist,
        )

        # status = "live" when is_tracking=True, "offline" otherwise (idle removed)
        status = "live" if tracker.is_tracking else "offline"
        return self._json_response({
            "ok": True,
            "status": status,
            "status_label": "Live" if tracker.is_tracking else "Offline",
            "is_tracking": tracker.is_tracking,
            "points": points,
            "last_lat": tracker.last_latitude or 0,
            "last_lng": tracker.last_longitude or 0,
            "last_seen": _localize_dt(tracker.last_seen, tz_name) if tracker.last_seen else "",
            "total_distance_km": total_dist,
        })

    @http.route("/salesperson_tracking/start", type="http", auth="user", methods=["POST"], csrf=False)
    def start_tracking(self, **kwargs):
        """Start tracking — idempotent. Multiple tabs calling this won't create duplicate sessions."""
        user = self._check_access()
        payload = self._json_body()
        tracker_id = payload.get("tracker_id")

        if tracker_id:
            tracker = request.env["salesperson.tracker"].sudo().browse(int(tracker_id))
        else:
            tracker = user._ensure_salesperson_tracker()

        if not tracker or not tracker.exists():
            _logger.error("start_tracking: no tracker for user=%s", user.name)
            return self._json_response({"ok": False, "error": "No tracker found"})

        was_tracking = tracker.is_tracking
        tracker.action_start_tracking()

        _logger.info(
            "start_tracking: user=%s tracker=%s was_tracking=%s → is_tracking=%s session=%s",
            user.name, tracker.id, was_tracking, tracker.is_tracking,
            tracker.current_session_id.id if tracker.current_session_id else None,
        )
        return self._json_response({
            "ok": True,
            "tracker_id": tracker.id,
            "already_active": was_tracking,
            "session_id": tracker.current_session_id.id if tracker.current_session_id else None,
            "checkin_time": str(tracker.checkin_time) if tracker.checkin_time else None,
        })

    @http.route("/salesperson_tracking/stop", type="http", auth="user", methods=["POST"], csrf=False)
    def stop_tracking(self, **kwargs):
        """Stop tracking — broadcasts stop to all open tabs via the status endpoint."""
        user = self._check_access()
        payload = self._json_body()
        tracker_id = payload.get("tracker_id")

        if tracker_id:
            tracker = request.env["salesperson.tracker"].sudo().browse(int(tracker_id))
        else:
            tracker = user._ensure_salesperson_tracker()

        if not tracker or not tracker.exists():
            _logger.error("stop_tracking: no tracker for user=%s", user.name)
            return self._json_response({"ok": False, "error": "No tracker found"})

        tracker.action_stop_tracking()
        _logger.info(
            "stop_tracking: user=%s tracker=%s stopped → checkout_time=%s",
            user.name, tracker.id, tracker.checkout_time,
        )
        return self._json_response({
            "ok": True,
            "checkout_time": str(tracker.checkout_time) if tracker.checkout_time else None,
        })

    @http.route("/salesperson_tracking/status/<int:tracker_id>", type="http", auth="user", methods=["GET"], csrf=False)
    def tracking_status(self, tracker_id, **kwargs):
        """Lightweight polling endpoint. All open tabs use this to stay in sync.
        Returns is_tracking + checkin/checkout so every tab reflects the real DB state
        regardless of which tab started or stopped tracking."""
        user = self._check_access()
        tracker = request.env["salesperson.tracker"].sudo().browse(tracker_id)
        if not tracker.exists():
            return self._json_response({"ok": False, "error": "Tracker not found"})

        tz_name = self._user_tz(user)
        _logger.debug(
            "tracking_status: tracker=%s is_tracking=%s status=%s",
            tracker_id, tracker.is_tracking, tracker.tracking_status,
        )
        return self._json_response({
            "ok": True,
            "is_tracking": tracker.is_tracking,
            "status": tracker.tracking_status,
            "status_label": tracker.tracking_status_label,
            "last_lat": tracker.last_latitude or 0,
            "last_lng": tracker.last_longitude or 0,
            "last_seen": _localize_dt(tracker.last_seen, tz_name) if tracker.last_seen else "",
            "checkin_time": _localize_dt(tracker.checkin_time, tz_name) if tracker.checkin_time else "",
            "checkout_time": _localize_dt(tracker.checkout_time, tz_name) if tracker.checkout_time else "",
            "location_name": tracker.location_name or "",
        })

    @http.route("/salesperson_tracking/save_photo", type="json", auth="user", methods=["POST"], csrf=False)
    def save_photo(self, image_data=None, filename=None, latitude=None, longitude=None, location_name=None, **kwargs):
        user = self._check_access()
        if not image_data:
            return {"ok": False, "error": "No image data"}

        tracker = user._ensure_salesperson_tracker()

        # Strip data URL header  (data:image/jpeg;base64,...)
        if ',' in image_data:
            image_data = image_data.split(',', 1)[1]

        try:
            image_bytes = base64.b64decode(image_data)
            image_b64   = base64.b64encode(image_bytes).decode()
        except Exception:
            return {"ok": False, "error": "Invalid image data"}

        checkin = tracker.current_checkin_id
        if checkin:
            checkin.write({
                "selfie_image":     image_b64,
                "selfie_filename":  filename or f"photo_{fields.Datetime.now()}.jpg",
                "selfie_latitude":  latitude  or 0.0,
                "selfie_longitude": longitude or 0.0,
                "selfie_taken_at":  fields.Datetime.now(),
            })

        return {"ok": True}
