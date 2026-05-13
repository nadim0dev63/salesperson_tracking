from datetime import timedelta
import math
import logging
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
import requests

_logger = logging.getLogger(__name__)


class SalespersonTracker(models.Model):
    _name = "salesperson.tracker"
    _description = "Salesperson Tracking Report"
    _order = "id desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]


    # Basic Info (from Visit Plan)
    name = fields.Char(string="Reference", compute="_compute_name", store=True)
    user_id = fields.Many2one("res.users", string="Salesman", required=True, tracking=True)
    sales_person = fields.Char(string="Sales Person")
    plan_id = fields.Many2one("salesperson.visit.plan", string="Visit Plan", ondelete="set null")
    company_id = fields.Many2one("res.company", related="user_id.company_id", store=True)
    sale_team_id = fields.Many2one("crm.team", related="user_id.sale_team_id", store=True)

    purpose = fields.Selection(related="plan_id.purpose", string="Purpose", store=True)
    visit_date = fields.Date(string="Visit Date", required=True, default=fields.Date.context_today)
    priority = fields.Selection([
        ("0", "Normal"),
        ("1", "High"),
        ("2", "Urgent"),
    ], default="0")

    # Counts
    total_visits = fields.Integer(string="Total Visits", compute="_compute_counts", store=True)
    total_visited = fields.Integer(string="Visited", compute="_compute_counts", store=True)
    skipped_visits = fields.Integer(string="Skipped Visits", compute="_compute_counts", store=True)

    # Timing
    checkin_time = fields.Datetime(string="First Check-In")
    checkout_time = fields.Datetime(string="Last Check-Out")
    duration_hours = fields.Float(string="Duration (Hours)", compute="_compute_duration", store=True)

    # Expenses Claim (Left group - Planned)
    transport_cost = fields.Float(string="Transport Cost")
    food_cost = fields.Float(string="Food Cost")
    other_cost = fields.Float(string="Other Cost")
    total_cost = fields.Float(string="Total Cost", compute="_compute_costs", store=True)

    # Actual Expenses (Right group)
    actual_transport_cost = fields.Float(string="Actual Transport")
    actual_food_cost = fields.Float(string="Actual Food")
    actual_other_cost = fields.Float(string="Actual Other")
    actual_total_cost = fields.Float(string="Actual Total", compute="_compute_actual_costs", store=True)

    # Tracking Stats
    total_distance_km = fields.Float(string="Total Distance Covered (KM)", default=0.0)
    total_tracking_time_seconds = fields.Integer(string="Total Tracking Time (sec)", default=0)
    total_visit_time_minutes = fields.Float(string="Total Time Spent in Visits (min)", default=0.0)
    traveling_time_minutes = fields.Float(string="Traveling Time (min)", compute="_compute_traveling_time", store=True)

    # Live Tracking
    is_tracking = fields.Boolean(string="Tracking Active", default=False)
    last_seen = fields.Datetime(string="Last Update")
    last_latitude = fields.Float(string="Last Latitude", digits=(16, 7))
    last_longitude = fields.Float(string="Last Longitude", digits=(16, 7))
    last_accuracy = fields.Float(string="Accuracy (m)", digits=(16, 2))
    location_name = fields.Char(string="Current Location")
    openstreetmap_url = fields.Char(compute="_compute_map_url")

    # Tracking Status
    tracking_status = fields.Selection(
        [("live", "Live"), ("offline", "Offline")],
        compute="_compute_tracking_status",
        search="_search_tracking_status",
    )
    tracking_status_label = fields.Char(compute="_compute_tracking_status")

    # Current Session
    current_session_id = fields.Many2one("salesperson.tracking.session", string="Current Session")
    current_checkin_id = fields.Many2one("salesperson.checkin", string="Current Check-In")

    # State
    state = fields.Selection([
        ("planned", "Planned"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ], default="planned", tracking=True)

    # Tabs
    target_line_ids = fields.One2many("salesperson.tracker.target.line", "tracker_id", string="Target Lines")
    visited_line_ids = fields.One2many("salesperson.tracker.visited.line", "tracker_id", string="Visited Lines")
    session_ids = fields.One2many("salesperson.tracking.session", "tracker_id", string="Tracking Sessions")
    related_notes = fields.Html(string="Related Notes")

    @api.depends("user_id", "plan_id")
    def _compute_name(self):
        for tracker in self:
            tracker.name = f"{tracker.user_id.name} - {tracker.visit_date}" if tracker.user_id else "New"

    @api.depends("plan_id.target_line_ids.customer_ids", "visited_line_ids")
    def _compute_counts(self):
        for tracker in self:
            # Total visits from plan
            total = 0
            for line in tracker.plan_id.target_line_ids:
                total += len(line.customer_ids) if line.customer_ids else 0
            tracker.total_visits = total
            # Visited from visited lines
            tracker.total_visited = len(tracker.visited_line_ids)
            tracker.skipped_visits = tracker.total_visits - tracker.total_visited

    @api.depends("transport_cost", "food_cost", "other_cost")
    def _compute_costs(self):
        for tracker in self:
            tracker.total_cost = tracker.transport_cost + tracker.food_cost + tracker.other_cost

    @api.depends("actual_transport_cost", "actual_food_cost", "actual_other_cost")
    def _compute_actual_costs(self):
        for tracker in self:
            tracker.actual_total_cost = tracker.actual_transport_cost + tracker.actual_food_cost + tracker.actual_other_cost

    @api.depends("checkin_time", "checkout_time")
    def _compute_duration(self):
        for tracker in self:
            if tracker.checkin_time and tracker.checkout_time:
                delta = tracker.checkout_time - tracker.checkin_time
                tracker.duration_hours = delta.total_seconds() / 3600.0
            else:
                tracker.duration_hours = 0.0

    @api.depends("total_tracking_time_seconds", "total_visit_time_minutes")
    def _compute_traveling_time(self):
        for tracker in self:
            tracking_minutes = tracker.total_tracking_time_seconds / 60.0
            tracker.traveling_time_minutes = tracking_minutes - tracker.total_visit_time_minutes

    @api.depends("last_latitude", "last_longitude")
    def _compute_map_url(self):
        for tracker in self:
            if tracker.last_latitude and tracker.last_longitude:
                tracker.openstreetmap_url = (
                    f"https://www.openstreetmap.org/?mlat={tracker.last_latitude}"
                    f"&mlon={tracker.last_longitude}#map=16/{tracker.last_latitude}/{tracker.last_longitude}"
                )
            else:
                tracker.openstreetmap_url = False

    @api.depends("is_tracking")
    def _compute_tracking_status(self):
        """Two states only: live (is_tracking=True) or offline (is_tracking=False).
        Idle removed — salesperson is live until they explicitly press Stop."""
        for tracker in self:
            status = "live" if tracker.is_tracking else "offline"
            tracker.tracking_status = status
            tracker.tracking_status_label = "Live" if tracker.is_tracking else "Offline"

    def _search_tracking_status(self, operator, value):
        mapping = {
            "live":    [("is_tracking", "=", True)],
            "offline": [("is_tracking", "=", False)],
        }
        if operator != "=" or value not in mapping:
            return []
        return mapping[value]

    def update_live_location(self, latitude, longitude, accuracy=None, source="browser", distance=0.0, retry_count=0):
        """Update live location with retry logic for serialization failures.

        IMPORTANT: We intentionally do NOT write salesperson coordinates to
        res.partner.partner_latitude / partner_longitude.  Those fields belong
        to *customer* partners and writing the salesperson's GPS position there
        corrupts the customer pins shown on the moving map.

        Every call — whether the position changed or not — creates a new log entry.
        The JS sends every 2 seconds, and each send must produce a row so the
        moving-map path and log count are always up to date.
        """
        self.ensure_one()
        _logger.debug(
            "update_live_location: tracker=%s user=%s lat=%.6f lng=%.6f accuracy=%s source=%s retry=%s",
            self.id, self.user_id.name, latitude, longitude, accuracy, source, retry_count,
        )

        try:
            # Invalidate the ORM cache so the next field read hits the DB.
            self.invalidate_recordset()

            now = fields.Datetime.now()

            # ── Location name: NEVER call _reverse_geocode here ──────────────
            # Geocoding makes a blocking HTTP request (up to 5 s timeout) which
            # holds the DB transaction open and causes serialization failures that
            # silently drop every log entry. Reuse the last known name; the JS
            # calls /salesperson_tracking/geocode separately (throttled, async).
            location_name = self.location_name or ""

            # 1. Update tracker — fast, no HTTP calls
            self.write({
                "is_tracking": True,
                "last_seen": now,
                "last_latitude": latitude,
                "last_longitude": longitude,
                "last_accuracy": accuracy or 0.0,
                "total_distance_km": self.total_distance_km + (distance or 0.0),
                "location_name": location_name,
            })
            _logger.debug("update_live_location: tracker updated ok")

            # 2. Append location log entry — always, every call
            log_vals = {
                "tracker_id": self.id,
                "tracked_at": now,
                "latitude": latitude,
                "longitude": longitude,
                "accuracy": accuracy or 0.0,
                "source": source,
                "location_name": location_name,
            }
            log = self.env["salesperson.location.log"].sudo().create(log_vals)
            _logger.debug("update_live_location: log created id=%s", log.id)

            # 3. Geofence auto check-in / check-out
            try:
                self._check_geofence(latitude, longitude)
            except Exception as gf_err:
                _logger.warning(
                    "update_live_location: geofence check failed for tracker %s: %s",
                    self.id, gf_err, exc_info=True,
                )

            # 4. Update active session's last position
            if self.current_session_id and self.current_session_id.state == "active":
                self.current_session_id.write({
                    "last_location_lat": latitude,
                    "last_location_lng": longitude,
                })
                _logger.debug("update_live_location: session %s position updated", self.current_session_id.id)

            _logger.info(
                "update_live_location: SUCCESS tracker=%s user=%s lat=%.6f lng=%.6f log_id=%s",
                self.id, self.user_id.name, latitude, longitude, log.id,
            )

        except Exception as e:
            _logger.error(
                "update_live_location: FAILED tracker=%s retry=%s error=%s",
                self.id, retry_count, e, exc_info=True,
            )
            self.env.cr.rollback()
            if "could not serialize access" in str(e) and retry_count < 3:
                import time
                wait = 0.1 * (2 ** retry_count)
                _logger.info(
                    "update_live_location: serialization conflict, retrying in %.2fs (attempt %s/3)",
                    wait, retry_count + 1,
                )
                time.sleep(wait)
                self.invalidate_recordset()
                return self.update_live_location(
                    latitude, longitude, accuracy, source, distance, retry_count + 1
                )
            raise

    @api.model
    def _get_geofence_radius(self):
        """Get geofence radius from system parameter."""
        return float(self.env["ir.config_parameter"].sudo().get_param(
            "zencore_salesperson_tracking.geofence_radius", "100"
        ))
    
    def _check_geofence(self, latitude, longitude):
        """Auto check-in/out based on 100m radius from customers."""
        def haversine(lat1, lon1, lat2, lon2):
            R = 6371000
            dlat = math.radians(lat2 - lat1)
            dlon = math.radians(lon2 - lon1)
            a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * \
                math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
            return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        # Get all target customers from plan
        customers = self.env["res.partner"]
        for line in self.plan_id.target_line_ids:
            customers |= line.customer_ids

        # Find nearest customer
        nearest_customer = None
        nearest_distance = float("inf")

        for customer in customers:
            if customer.partner_latitude and customer.partner_longitude:
                distance = haversine(
                    latitude, longitude,
                    customer.partner_latitude,
                    customer.partner_longitude
                )
                if distance < nearest_distance:
                    nearest_distance = distance
                    nearest_customer = customer

        # Check if within geofence
        geofence_radius = self._get_geofence_radius()
        if nearest_customer and nearest_distance <= geofence_radius:
            # Check if already checked into this customer
            if not self.current_checkin_id or self.current_checkin_id.partner_id != nearest_customer:
                # Check out from current
                if self.current_checkin_id:
                    self.current_checkin_id.action_checkout(latitude, longitude)

                # Find the target line for this customer
                target_line = self.env["salesperson.visit.plan.line"].search([
                    ("plan_id", "=", self.plan_id.id),
                    ("customer_ids", "in", nearest_customer.id),
                ], limit=1)

                # Create check-in
                checkin = self.env["salesperson.checkin"].create({
                    "tracker_id": self.id,
                    "partner_id": nearest_customer.id,
                    "target_line_id": target_line.id if target_line else False,
                    "location_name": nearest_customer.display_name,
                    "checkin_latitude": latitude,
                    "checkin_longitude": longitude,
                    "checkin_time": fields.Datetime.now(),
                    "state": "checked_in",
                })
                self.current_checkin_id = checkin.id

                # Update first check-in time on tracker
                if not self.checkin_time:
                    self.checkin_time = fields.Datetime.now()

        elif self.current_checkin_id and self.current_checkin_id.state == "checked_in":
            # Check if exited geofence
            if self.current_checkin_id.partner_id:
                distance = haversine(
                    latitude, longitude,
                    self.current_checkin_id.partner_id.partner_latitude,
                    self.current_checkin_id.partner_id.partner_longitude
                )
                if distance > geofence_radius:
                    self.current_checkin_id.action_checkout(latitude, longitude)
                    self.current_checkin_id = False

    def _reverse_geocode(self, latitude, longitude):
        """Get location name from coordinates."""
        try:
            response = requests.get(
                "https://nominatim.openstreetmap.org/reverse",
                headers={"User-Agent": "Odoo ZenCore Tracking"},
                params={
                    "format": "jsonv2",
                    "lat": latitude,
                    "lon": longitude,
                    "zoom": 18,
                    "accept-language": "en",
                },
                timeout=5,
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("display_name", "").split(",")[0]
        except Exception:
            pass
        return False

    def action_start_tracking(self):
        """Start a new tracking session (idempotent — safe to call when already active)."""
        self.ensure_one()
        _logger.info("action_start_tracking: tracker=%s user=%s is_tracking=%s", self.id, self.user_id.name, self.is_tracking)

        # If already tracking, do nothing — prevents duplicate sessions when the
        # live page is opened multiple times and each tab calls /start.
        if self.is_tracking and self.current_session_id and self.current_session_id.state == "active":
            _logger.info(
                "action_start_tracking: tracker=%s already active (session=%s) — no-op",
                self.id, self.current_session_id.id,
            )
            return

        # Close any orphaned open session before creating a new one
        old_session = self.current_session_id
        if old_session and old_session.state == "active":
            _logger.warning(
                "action_start_tracking: closing orphaned session=%s for tracker=%s",
                old_session.id, self.id,
            )
            old_session.action_stop()

        session = self.env["salesperson.tracking.session"].create({
            "tracker_id": self.id,
            "start_time": fields.Datetime.now(),
            "state": "active",
        })
        _logger.info("action_start_tracking: created session=%s for tracker=%s", session.id, self.id)

        vals = {
            "is_tracking": True,
            "current_session_id": session.id,
            "state": "in_progress",
        }
        if not self.checkin_time:
            vals["checkin_time"] = fields.Datetime.now()
        self.write(vals)

    def action_stop_tracking(self):
        """Stop the current tracking session. Sets state back to 'planned' so the
        status bar shows Offline (not 'In Progress') after stopping."""
        self.ensure_one()
        _logger.info("action_stop_tracking: tracker=%s user=%s session=%s", self.id, self.user_id.name, self.current_session_id.id if self.current_session_id else None)

        if self.current_session_id:
            self.current_session_id.action_stop()

        self.write({
            "is_tracking": False,
            "current_session_id": False,
            "checkout_time": fields.Datetime.now(),
            # Return to 'planned' so the form view status bar shows the tracker
            # as not active.  Use 'completed' only when the full day visit is done.
            "state": "planned",
        })

        if self.current_checkin_id and self.current_checkin_id.state == "checked_in":
            self.current_checkin_id.action_checkout()

        _logger.info("action_stop_tracking: tracker=%s stopped successfully", self.id)

    def action_load_target_lines(self):
        """Load target lines from visit plan."""
        self.ensure_one()
        if not self.plan_id:
            return

        # Clear existing target lines
        self.target_line_ids.unlink()

        for plan_line in self.plan_id.target_line_ids:
            self.env["salesperson.tracker.target.line"].create({
                "tracker_id": self.id,
                "plan_line_id": plan_line.id,
                "expected_time_minutes": plan_line.expected_time_minutes,
                "employee_ids": [(6, 0, plan_line.employee_ids.ids)],
                "from_location": plan_line.from_location,
                "to_location": plan_line.to_location,
                "expected_km": plan_line.expected_km,
                "customer_ids": [(6, 0, plan_line.customer_ids.ids)],
                "notes": plan_line.notes,
            })

    def action_open_live_tracking_page(self):
        self.ensure_one()
        # Open without tracker_id when viewing your own tracker so the server
        # always resolves is_owner=True via _ensure_salesperson_tracker().
        if int(self.user_id.id) == int(self.env.user.id):
            url = "/salesperson_tracking/live"
        else:
            url = f"/salesperson_tracking/live?tracker_id={self.id}"
        return {
            "type": "ir.actions.act_url",
            "url": url,
            "target": "self",
        }

    def action_view_moving_map(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/salesperson_tracking/moving_map/{self.id}",
            "target": "new",
        }

    def action_view_location_history(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Location History",
            "res_model": "salesperson.location.log",
            "view_mode": "list,form",
            "domain": [("tracker_id", "=", self.id)],
            "context": {"default_tracker_id": self.id},
        }

    def action_print_tracking_report(self):
        return self.env.ref("zencore_salesperson_tracking.action_report_tracking_summary").report_action(self)

    @api.model
    def _cleanup_stale_sessions(self):
        """Clean up sessions that have been idle for too long."""
        thirty_min_ago = fields.Datetime.now() - timedelta(minutes=30)
        
        stale_trackers = self.search([
            ('is_tracking', '=', True),
            ('last_seen', '<', thirty_min_ago),
        ])
        
        for tracker in stale_trackers:
            try:
                tracker.action_stop_tracking()
                _logger.info("Auto-stopped tracking for %s (tracker %s) due to inactivity", tracker.user_id.name, tracker.id)
            except Exception as e:
                _logger.error("Failed to auto-stop tracker %s: %s", tracker.id, e, exc_info=True)
        
        return True

class SalespersonTrackerTargetLine(models.Model):
    _name = "salesperson.tracker.target.line"
    _description = "Tracker Target Line"

    tracker_id = fields.Many2one("salesperson.tracker", required=True, ondelete="cascade")
    plan_line_id = fields.Many2one("salesperson.visit.plan.line", string="Plan Line")

    expected_time_minutes = fields.Integer(string="Expected Time (min)")
    employee_ids = fields.Many2many("hr.employee", string="Accompanies")
    from_location = fields.Char(string="From Location")
    to_location = fields.Char(string="To Location")
    expected_km = fields.Float(string="Expected KM", digits=(16, 2))
    customer_ids = fields.Many2many("res.partner", string="Customers")
    notes = fields.Text(string="Notes")


class SalespersonTrackerVisitedLine(models.Model):
    _name = "salesperson.tracker.visited.line"
    _description = "Tracker Visited Line"

    tracker_id = fields.Many2one("salesperson.tracker", required=True, ondelete="cascade")
    checkin_id = fields.Many2one("salesperson.checkin", string="Check-In Record")
    partner_id = fields.Many2one("res.partner", string="Customer", required=True)

    checkin_time = fields.Datetime(string="In", help="Auto-recorded when entering 100m radius")
    checkout_time = fields.Datetime(string="Exit", help="Auto-recorded when exiting 100m radius")
    time_spent_minutes = fields.Float(string="Time Spent (min)", compute="_compute_time_spent", store=True)

    rating = fields.Selection([
        ("1", "★ Poor"),
        ("2", "★★ Fair"),
        ("3", "★★★ Good"),
        ("4", "★★★★ Very Good"),
        ("5", "★★★★★ Excellent"),
    ], string="Rating")

    @api.depends("checkin_time", "checkout_time")
    def _compute_time_spent(self):
        for line in self:
            if line.checkin_time and line.checkout_time:
                delta = line.checkout_time - line.checkin_time
                line.time_spent_minutes = delta.total_seconds() / 60.0
            else:
                line.time_spent_minutes = 0.0