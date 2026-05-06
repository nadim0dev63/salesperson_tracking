import base64
import json
from datetime import date
import math
from odoo import fields, http, _
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request
import json as json_lib, base64 as b64_lib


class SalespersonTrackingController(http.Controller):

    def _check_salesperson_access(self):
        user = request.env.user

        if not (
            user.has_group("sales_team.group_sale_salesman") or
            user.has_group("sales_team.group_sale_manager")
        ):
            raise AccessError(_("Only Salespersons and Sales Managers can use live tracking."))

        return user
    
    def _json_body(self):
        payload = request.httprequest.data or b"{}"
        return json.loads(payload.decode("utf-8"))

    @http.route("/salesperson_tracking/live", type="http", auth="user", website=False)
    def salesperson_tracking_live_page(self, tracker_id=None, **kwargs):
        user = self._check_salesperson_access()

        if tracker_id:
            tracker = request.env["salesperson.tracker"].sudo().browse(int(tracker_id))
            if not tracker.exists():
                return request.not_found()
            card_user = tracker.user_id
        else:
            tracker = user.sudo()._ensure_salesperson_tracker()
            card_user = user

        today = fields.Date.context_today(request.env.user)

        plans = request.env["salesperson.visit.plan"].sudo().search(
            [("user_id", "=", card_user.id), ("visit_date", "=", today)]
        )
        plan_data = [
            {
                "id":      p.id,
                "name":    p.location_name,
                "lat":     p.latitude,
                "lng":     p.longitude,
                "covered": p.is_covered,
                "stay":    p.stay_duration_display,
                "radius":  p.radius_meters,
            }
            for p in plans
            if p.latitude or p.longitude
        ]

        active_checkin = request.env["salesperson.checkin"].sudo().search(
            [("user_id", "=", card_user.id), ("state", "=", "checked_in")], limit=1
        )

        today_start = fields.Datetime.to_datetime(today)
        today_logs = request.env["salesperson.location.log"].sudo().search(
            [("tracker_id", "=", tracker.id), ("create_date", ">=", today_start)],
            order="create_date asc",
        )
        total_distance_km = self._compute_total_distance_km(today_logs)

        plan_b64 = b64_lib.b64encode(json_lib.dumps(plan_data).encode()).decode()
        my_tracking_action = request.env.ref("salesperson_live_tracking.action_salesperson_tracker_my")

        values = {
            "tracker":             tracker,
            "user":                card_user,  # ← card-এর user
            "my_tracking_url":     "/web#action=%s&model=salesperson.tracker&view_type=list" % my_tracking_action.id,
            "plan_points_b64":     plan_b64,
            "active_checkin":      active_checkin,
            "today_plan_count":    len(plans),
            "today_covered_count": len(plans.filtered("is_covered")),
            "total_distance_km":   total_distance_km,
        }
        return request.render("salesperson_live_tracking.live_tracking_page", values)
        
    @staticmethod
    def _haversine_km(lat1, lon1, lat2, lon2):
        R = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


    def _compute_total_distance_km(self, logs):
        """Sum haversine distance over ordered GPS log records (accuracy <= 200 m filtered)."""
        total, prev = 0.0, None
        for log in logs:
            if not (log.latitude and log.longitude):
                continue
            if log.accuracy and log.accuracy > 200:   # skip poor-accuracy points
                continue
            if prev:
                total += self._haversine_km(prev[0], prev[1], log.latitude, log.longitude)
            prev = (log.latitude, log.longitude)
        return round(total, 3)
    
    @http.route("/salesperson_tracking/update", type="http", auth="user", methods=["POST"], csrf=False)
    def salesperson_tracking_update(self, **kwargs):
        user    = self._check_salesperson_access()
        payload = self._json_body()

        try:
            latitude  = float(payload["latitude"])
            longitude = float(payload["longitude"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValidationError(_("Latitude and longitude are required.")) from error

        if not -90.0 <= latitude <= 90.0:
            raise ValidationError(_("Latitude must be between -90 and 90."))
        if not -180.0 <= longitude <= 180.0:
            raise ValidationError(_("Longitude must be between -180 and 180."))

        # ← tracker_id payload থেকে নাও
        tracker_id = payload.get("tracker_id")
        if tracker_id:
            tracker = request.env["salesperson.tracker"].sudo().browse(int(tracker_id))
            if not tracker.exists():
                return request.make_json_response({"ok": False, "error": "Tracker not found"})
        else:
            tracker = user.sudo()._ensure_salesperson_tracker()

        tracker.sudo().update_live_location(
            latitude=latitude,
            longitude=longitude,
            accuracy=payload.get("accuracy"),
            speed=payload.get("speed"),
            heading=payload.get("heading"),
            source=payload.get("source") or "browser",
            distance=float(payload.get("distance") or 0.0),
        )

        today_start = fields.Datetime.to_datetime(date.today())
        today_logs  = request.env["salesperson.location.log"].sudo().search(
            [
                ("tracker_id", "=", tracker.id),
                ("create_date", ">=", today_start),
            ],
            order="create_date asc",
        )
        total_distance_km = self._compute_total_distance_km(today_logs)

        return request.make_json_response({
            "ok":                True,
            "tracker_id":        tracker.id,
            "status":            tracker.tracking_status,
            "status_label":      tracker.tracking_status_label,
            "last_seen":         fields.Datetime.to_string(tracker.last_seen),
            "latitude":          tracker.partner_id.partner_latitude,
            "longitude":         tracker.partner_id.partner_longitude,
            "location_name":     tracker.location_name,
            "map_url":           tracker.openstreetmap_url,
            "total_distance_km": total_distance_km,
        })

  
    @http.route(
        "/salesperson_tracking/stop",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False
    )
    def salesperson_tracking_stop(self, **kwargs):

        user = self._check_salesperson_access()
        payload = self._json_body()

        tracker = user.sudo()._ensure_salesperson_tracker()

        duration_seconds = 0

        print("#$ERERERERERERERERER STOP !")

        try:
            duration_seconds = int(payload.get("duration_seconds") or 0)
        except (TypeError, ValueError):
            duration_seconds = 0

        if duration_seconds <= 0 and tracker.last_tracking_start:
            delta = fields.Datetime.now() - tracker.last_tracking_start
            duration_seconds = int(delta.total_seconds())

        tracker.sudo().action_stop_tracking(duration_seconds)

        return request.make_json_response({
            "ok": True,
            "duration_saved": duration_seconds,
            "status": tracker.tracking_status,
        })
    

    @http.route("/salesperson_tracking/checkin", type="http", auth="user", methods=["POST"], csrf=False)
    def salesperson_tracking_checkin(self, **kwargs):
        """
        Req #7: Check-In with auto time & location capture.
        JSON body: { latitude, longitude, location_name, visit_plan_id (optional) }
        """
        user = self._check_salesperson_access()
        payload = self._json_body()
        try:
            latitude = float(payload["latitude"])
            longitude = float(payload["longitude"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValidationError(_("Latitude and longitude are required.")) from error

        tracker = user.sudo()._ensure_salesperson_tracker()
        location_name = payload.get("location_name") or tracker.location_name or "Unknown Location"

        existing = request.env["salesperson.checkin"].sudo().search(
            [("user_id", "=", user.id), ("state", "=", "checked_in")], limit=1
        )
        if existing:
            existing.action_checkout(latitude=latitude, longitude=longitude)

        checkin = request.env["salesperson.checkin"].sudo().create({
            "tracker_id": tracker.id,
            "location_name": location_name,
            "checkin_latitude": latitude,
            "checkin_longitude": longitude,
            "checkin_time": fields.Datetime.now(),
            "state": "checked_in",
            "visit_plan_id": payload.get("visit_plan_id") or False,
        })
        return request.make_json_response({
            "ok": True,
            "checkin_id": checkin.id,
            "checkin_name": checkin.name,
            "location_name": location_name,
            "checkin_time": fields.Datetime.to_string(checkin.checkin_time),
        })
    
    @http.route("/salesperson_tracking/checkout", type="http", auth="user", methods=["POST"], csrf=False)
    def salesperson_tracking_checkout(self, **kwargs):
        """
        Req #7: Check-Out with automatic time capture.
        JSON body: { checkin_id, latitude, longitude, notes (opt), meeting_outcome (opt) }
        """
        user = self._check_salesperson_access()
        payload = self._json_body()
        checkin_id = payload.get("checkin_id")
        if checkin_id:
            checkin = request.env["salesperson.checkin"].sudo().browse(int(checkin_id))
        else:
            checkin = request.env["salesperson.checkin"].sudo().search(
                [("user_id", "=", user.id), ("state", "=", "checked_in")], limit=1
            )
        if not checkin or not checkin.exists():
            return request.make_json_response({"ok": False, "error": "No active check-in found."})

        write_vals = {}
        if payload.get("notes"):
            write_vals["notes"] = payload["notes"]
        if payload.get("meeting_outcome"):
            write_vals["meeting_outcome"] = payload["meeting_outcome"]
        if payload.get("customer_feedback"):
            write_vals["customer_feedback"] = payload["customer_feedback"]
        if write_vals:
            checkin.write(write_vals)

        lat = payload.get("latitude")
        lng = payload.get("longitude")
        checkin.action_checkout(
            latitude=float(lat) if lat else None,
            longitude=float(lng) if lng else None,
        )
        return request.make_json_response({
            "ok": True,
            "checkin_id": checkin.id,
            "duration": checkin.duration_display,
            "checkout_time": fields.Datetime.to_string(checkin.checkout_time),
        })
    

    @http.route("/salesperson_tracking/selfie", type="http", auth="user", methods=["POST"], csrf=False)
    def salesperson_tracking_selfie(self, **kwargs):
        """
        Req #3: Geo-Tagged Selfie Proof.
        JSON body: { checkin_id, image_b64, latitude, longitude }
        """
        user = self._check_salesperson_access()
        payload = self._json_body()
        checkin_id = payload.get("checkin_id")
        image_b64 = payload.get("image_b64", "")
        lat = payload.get("latitude")
        lng = payload.get("longitude")

        if not checkin_id:
            checkin = request.env["salesperson.checkin"].sudo().search(
                [("user_id", "=", user.id), ("state", "=", "checked_in")], limit=1
            )
        else:
            checkin = request.env["salesperson.checkin"].sudo().browse(int(checkin_id))

        if not checkin or not checkin.exists():
            return request.make_json_response({"ok": False, "error": "No active check-in."})
        
        if "," in image_b64:
            image_b64 = image_b64.split(",", 1)[1]

        checkin.write({
            "selfie_image": image_b64,
            "selfie_filename": "selfie_%s.jpg" % checkin.id,
            "selfie_taken_at": fields.Datetime.now(),
            "selfie_latitude": float(lat) if lat else 0.0,
            "selfie_longitude": float(lng) if lng else 0.0,
        })
        return request.make_json_response({
            "ok": True,
            "checkin_id": checkin.id,
            "selfie_taken_at": fields.Datetime.to_string(checkin.selfie_taken_at),
        })

    @http.route("/salesperson_tracking/sync_offline", type="http", auth="user", methods=["POST"], csrf=False)
    def salesperson_tracking_sync_offline(self, **kwargs):
        """
        Req #8: Offline Mode Support.
        Accepts a batch of queued location updates collected while offline.
        JSON body: { events: [ {type, payload, queued_at}, … ] }
        """
        user = self._check_salesperson_access()
        payload = self._json_body()
        events = payload.get("events") or []
        tracker = user.sudo()._ensure_salesperson_tracker()
        processed = 0
        errors = []
        for event in events:
            try:
                etype = event.get("type")
                ep = event.get("payload") or {}
                if etype == "location":
                    tracker.sudo().update_live_location(
                        latitude=float(ep["latitude"]),
                        longitude=float(ep["longitude"]),
                        accuracy=ep.get("accuracy"),
                        speed=ep.get("speed"),
                        heading=ep.get("heading"),
                        source="offline_sync",
                    )
                elif etype == "checkin":
                    request.env["salesperson.checkin"].sudo().create({
                        "tracker_id": tracker.id,
                        "location_name": ep.get("location_name") or "Offline Check-In",
                        "checkin_latitude": float(ep.get("latitude") or 0),
                        "checkin_longitude": float(ep.get("longitude") or 0),
                        "checkin_time": ep.get("checkin_time") or fields.Datetime.to_string(fields.Datetime.now()),
                        "state": "checked_in",
                    })
                elif etype == "checkout":
                    cid = ep.get("checkin_id")
                    if cid:
                        ci = request.env["salesperson.checkin"].sudo().browse(int(cid))
                        if ci.exists() and ci.state == "checked_in":
                            if ep.get("notes"):
                                ci.write({"notes": ep["notes"]})
                            if ep.get("meeting_outcome"):
                                ci.write({"meeting_outcome": ep["meeting_outcome"]})
                            ci.action_checkout(
                                latitude=float(ep["latitude"]) if ep.get("latitude") else None,
                                longitude=float(ep["longitude"]) if ep.get("longitude") else None,
                            )
                processed += 1
            except Exception as e:
                errors.append(str(e))
        return request.make_json_response({
            "ok": True,
            "processed": processed,
            "errors": errors,
        })
    
    @http.route("/salesperson_tracking/my_plans", type="http", auth="user", methods=["GET"], csrf=False)
    def salesperson_tracking_my_plans(self, **kwargs):
        """Return today's visit plans as JSON for the mobile tracking page."""
        user = self._check_salesperson_access()
        today = fields.Date.context_today(request.env.user)
        plans = request.env["salesperson.visit.plan"].sudo().search(
            [("user_id", "=", user.id), ("visit_date", "=", today)]
        )
        data = [
            {
                "id": p.id,
                "name": p.location_name,
                "lat": p.latitude,
                "lng": p.longitude,
                "covered": p.is_covered,
                "stay": p.stay_duration_display,
                "radius": p.radius_meters,
                "priority": p.priority,
                "notes": p.manager_notes or "",
            }
            for p in plans
            if p.latitude or p.longitude
        ]
        return request.make_json_response({"ok": True, "plans": data})

    
    @http.route("/salesperson_tracking/moving_map/<int:tracker_id>", type="http", auth="user", website=False)
    def salesperson_tracking_moving_map(self, tracker_id, **kwargs):
        user = self._check_salesperson_access()
        tracker = request.env["salesperson.tracker"].sudo().browse(tracker_id)
        if not tracker.exists():
            return request.not_found()
        today_start = fields.Datetime.to_string(
            fields.Datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        )
        logs = request.env["salesperson.location.log"].sudo().search(
            [("tracker_id", "=", tracker_id)],
            order="tracked_at asc",
        )
        location_points = [
            {
                "lat": log.latitude,
                "lng": log.longitude,
                "accuracy": log.accuracy,
                "speed": log.speed,
                "time": fields.Datetime.to_string(log.tracked_at),
                "location_name": log.location_name or "",
            }
            for log in logs
            if log.latitude and log.longitude
        ]
        # Also fetch today's visit plans for coverage overlay
        today = fields.Date.context_today(request.env.user)
        plans = request.env["salesperson.visit.plan"].sudo().search(
            [("user_id", "=", tracker.user_id.id), ("visit_date", "=", today)]
        )
        plan_markers = [
            {
                "lat": p.latitude,
                "lng": p.longitude,
                "name": p.location_name,
                "covered": p.is_covered,
                "stay": p.stay_duration_display,
                "radius": p.radius_meters,
            }
            for p in plans
            if p.latitude or p.longitude
        ]
        
        json_b64 = b64_lib.b64encode(json_lib.dumps(location_points).encode()).decode()
        plans_b64 = b64_lib.b64encode(json_lib.dumps(plan_markers).encode()).decode()
        values = {
            "tracker": tracker,
            "user": user,
            "location_points_b64": json_b64,
            "plan_markers_b64": plans_b64,
            "total_logs": len(location_points),
        }
        return request.render("salesperson_live_tracking.moving_map_page", values)



class SalespersonDashboard(http.Controller):

    @http.route('/salesperson/dashboard', type='http', auth='user', website=False)
    def dashboard(self, **kwargs):
        user = request.env.user
        today = fields.Date.context_today(user)
        today_dt = fields.Datetime.to_datetime(today)

        is_manager = user.has_group('sales_team.group_sale_manager')
        is_salesperson = user.has_group('sales_team.group_sale_salesman')

        TrackerModel = request.env['salesperson.tracker'].sudo()
        if is_manager:
            trackers = TrackerModel.search([])
        else:
            trackers = TrackerModel.search([('user_id', '=', user.id)])

        # ── Status counts ───────────────────────────────────────────────────
        live_count    = sum(1 for t in trackers if t.tracking_status == 'live')
        idle_count    = sum(1 for t in trackers if t.tracking_status == 'idle')
        offline_count = sum(1 for t in trackers if t.tracking_status == 'offline')

        # ── Today's visit plan totals ────────────────────────────────────────
        PlanModel = request.env['salesperson.visit.plan'].sudo()
        print("#E$#$#$#$#$#$",PlanModel)
        if is_manager:
            today_plans   = PlanModel.search([('visit_date', '=', today)])
        else:
            today_plans   = PlanModel.search([('visit_date', '=', today), ('user_id', '=', user.id)])

        total_planned = len(today_plans)
        total_covered = len(today_plans.filtered('is_covered'))

        # ── Route deviation alerts ───────────────────────────────────────────
        deviation_alerts = sum(1 for t in trackers if t.route_deviation_alert)

        # ── Salesperson table rows ───────────────────────────────────────────
        import hashlib as _hs
        def avatar_gradient(name):
            colours = [
                ('135deg,#4f8ef7,#6ee7b7'), ('135deg,#f59e0b,#f87171'),
                ('135deg,#6ee7b7,#4f8ef7'), ('135deg,#a78bfa,#f87171'),
                ('135deg,#34d399,#60a5fa'), ('135deg,#fbbf24,#a78bfa'),
                ('135deg,#f87171,#fbbf24'), ('135deg,#60a5fa,#34d399'),
            ]
            idx = int(_hs.md5((name or '').encode()).hexdigest(), 16) % len(colours)
            return 'linear-gradient(%s)' % colours[idx]

        rows = []
        for t in trackers:
            u = t.user_id
            plans_sp   = PlanModel.search([('visit_date', '=', today), ('user_id', '=', u.id)])
            covered_sp = len(plans_sp.filtered('is_covered'))
            total_sp   = len(plans_sp)
            pct        = int(covered_sp * 100 / total_sp) if total_sp else 0
            initials   = ''.join(w[0].upper() for w in (u.name or 'SP').split()[:2])
            team_name  = t.sale_team_id.name if t.sale_team_id else ''

            # last seen label
            ls = t.last_seen
            if ls:
                delta = (fields.Datetime.now() - ls).total_seconds()
                if delta < 60:
                    ls_label = 'just now'
                elif delta < 3600:
                    ls_label = '%d min ago' % int(delta // 60)
                elif delta < 86400:
                    ls_label = '%d hr ago' % int(delta // 3600)
                else:
                    ls_label = '%d days ago' % int(delta // 86400)
            else:
                ls_label = 'Never'

            # distance today
            today_logs = request.env['salesperson.location.log'].sudo().search(
                [('tracker_id', '=', t.id), ('create_date', '>=', today_dt)],
                order='create_date asc',
            )
            dist_km = SalespersonTrackingController._compute_total_distance_km(
                SalespersonTrackingController, today_logs
            )

            rows.append({
                'id':         t.id,
                'name':       u.name or 'Unknown',
                'initials':   initials,
                'gradient':   avatar_gradient(u.name),
                'team':       team_name,
                'status':     t.tracking_status or 'offline',
                'state':      t.state or 'planned',
                'covered':    covered_sp,
                'total':      total_sp,
                'pct':        pct,
                'distance':   '%.1f km' % dist_km,
                'last_seen':  ls_label,
                'location':   t.location_name or '—',
                'lat':        t.latitude or 0.0,
                'lng':        t.longitude or 0.0,
            })

        # ── Recent activity feed (check-ins / checkouts) ─────────────────────
        CheckinModel = request.env['salesperson.checkin'].sudo()
        if is_manager:
            recent_checkins = CheckinModel.search(
                [('checkin_time', '>=', today_dt)],
                order='checkin_time desc', limit=15
            )
        else:
            recent_checkins = CheckinModel.search(
                [('user_id', '=', user.id), ('checkin_time', '>=', today_dt)],
                order='checkin_time desc', limit=15
            )

        activity_feed = []
        outcome_emoji = {
            'deal_closed':    '✅ Deal Closed',
            'positive':       '👍 Positive',
            'neutral':        '😐 Neutral',
            'negative':       '👎 Negative',
            'followup_needed':'🔁 Follow-up Needed',
        }
        for ci in recent_checkins:
            ts = ci.checkin_time
            time_str = ts.strftime('%H:%M') if ts else '—'
            if ci.state == 'checked_in':
                dot_color = 'var(--live)'
                desc = 'Checked in at %s' % (ci.location_name or '—')
            else:
                dot_color = 'var(--accent2)'
                outcome = outcome_emoji.get(ci.meeting_outcome or '', '')
                desc = 'Checked out · %s · %s' % (ci.duration_display, outcome) if outcome else 'Checked out · %s' % ci.duration_display
            activity_feed.append({
                'name':      ci.user_id.name or '—',
                'desc':      desc,
                'time':      time_str,
                'dot_color': dot_color,
            })

        # ── KPI totals ────────────────────────────────────────────────────────
        kpi_missed     = total_planned - total_covered
        kpi_in_progress = len(today_plans.filtered(lambda p: not p.is_covered))
        kpi_pct        = int(total_covered * 100 / total_planned) if total_planned else 0

        # ── My tracker (for salesperson personal stats) ───────────────────────
        my_tracker = None
        my_dist    = 0.0
        my_covered = 0
        my_total   = 0
        my_checkins_today = 0
        if not is_manager:
            my_tracker = TrackerModel.search([('user_id', '=', user.id)], limit=1)
            if my_tracker:
                my_logs = request.env['salesperson.location.log'].sudo().search(
                    [('tracker_id', '=', my_tracker.id), ('create_date', '>=', today_dt)],
                    order='create_date asc',
                )
                my_dist = SalespersonTrackingController._compute_total_distance_km(
                    SalespersonTrackingController, my_logs
                )
                my_plans = PlanModel.search([('visit_date', '=', today), ('user_id', '=', user.id)])
                my_total   = len(my_plans)
                my_covered = len(my_plans.filtered('is_covered'))
                my_checkins_today = CheckinModel.search_count(
                    [('user_id', '=', user.id), ('checkin_time', '>=', today_dt)]
                )

        values = {
            'user':               user,
            'is_manager':         is_manager,
            'is_salesperson':     is_salesperson,
            'today':              today.strftime('%B %d, %Y'),

            # stat cards
            'total_reps':         len(trackers),
            'live_count':         live_count,
            'idle_count':         idle_count,
            'offline_count':      offline_count,
            'total_planned':      total_planned,
            'total_covered':      total_covered,
            'deviation_alerts':   deviation_alerts,

            # table
            'rows':               rows,

            # activity
            'activity_feed':      activity_feed,

            # KPI
            'kpi_covered':        total_covered,
            'kpi_in_progress':    kpi_in_progress,
            'kpi_missed':         total_planned - total_covered,
            'kpi_pct':            kpi_pct,

            # salesperson personal
            'my_tracker':         my_tracker,
            'my_dist':            '%.1f' % my_dist,
            'my_covered':         my_covered,
            'my_total':           my_total,
            'my_checkins_today':  my_checkins_today,
            'my_status':          my_tracker.tracking_status if my_tracker else 'offline',
            'my_location':        my_tracker.location_name if my_tracker else '—',
        }
        import json as _json
        values['json'] = _json
        return request.render('salesperson_live_tracking.salesperson_dashboard', values)
    @http.route('/salesperson/dashboard/data', type='http', auth='user', methods=['GET'], csrf=False)
    def dashboard_data_json(self, **kwargs):
        user = request.env.user
        today = fields.Date.context_today(user)
        today_dt = fields.Datetime.to_datetime(today)
        is_manager = user.has_group('sales_team.group_sale_manager')

        TrackerModel = request.env['salesperson.tracker'].sudo()
        trackers = TrackerModel.search([]) if is_manager else TrackerModel.search([('user_id', '=', user.id)])

        PlanModel = request.env['salesperson.visit.plan'].sudo()
        LogModel  = request.env['salesperson.location.log'].sudo()  # ← NEW

        data = []
        for t in trackers:
            plans   = PlanModel.search([('visit_date', '=', today), ('user_id', '=', t.user_id.id)])
            covered = len(plans.filtered('is_covered'))
            total   = len(plans)

            # ── NEW: fetch today's location history ──────────────────────────
            logs = LogModel.search(
                [('tracker_id', '=', t.id), ('tracked_at', '>=', today_dt)],
                order='tracked_at asc',
                limit=500,  # safety cap — enough for a full day
            )
            points = [
                {
                    'lat': log.latitude,
                    'lng': log.longitude,
                    'time': fields.Datetime.to_string(log.tracked_at),
                    'loc':  log.location_name or '',
                    'spd':  round(log.speed * 3.6, 1) if log.speed else None,  # m/s → km/h
                }
                for log in logs
                if log.latitude and log.longitude
                and (not log.accuracy or log.accuracy <= 200)  # filter noisy points
            ]
            # ────────────────────────────────────────────────────────────────

            initials = ''.join(w[0].upper() for w in (t.user_id.name or 'SP').split()[:2])

            data.append({
                'id':       t.id,
                'name':     t.user_id.name or '',
                'initials': initials,
                'status':   t.tracking_status or 'offline',
                'lat':      t.latitude or 0.0,
                'lng':      t.longitude or 0.0,
                'location': t.location_name or '',
                'covered':  covered,
                'total':    total,
                'points':   points,   # ← NEW
            })

        live_count = sum(1 for d in data if d['status'] == 'live')
        return request.make_json_response({
            'ok':        True,
            'trackers':  data,
            'live_count': live_count,
        })