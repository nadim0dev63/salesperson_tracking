from odoo import fields, models, api
import logging

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = "res.users"

    salesperson_role = fields.Selection(
        [
            ("manager", "Manager"),
            ("salesman", "Salesman"),
        ],
        string="Role",
        default="salesman",
        required=True,
        help="Manager: Full access to all tracking data\nSalesman: Own records only",
    )

    salesperson_tracker_ids = fields.One2many(
        "salesperson.tracker",
        "user_id",
        string="Tracking Reports",
    )

    def action_open_live_tracking_page(self):
        """Open live tracking page."""
        self.ensure_one()
        self._ensure_salesperson_tracker()
        return {
            "type": "ir.actions.act_url",
            "url": "/salesperson_tracking/live",
            "target": "self",
        }

    def _ensure_salesperson_tracker(self):
        """Ensure a tracker exists for today for this user.

        Returns an existing tracker for today or creates a new one.
        Also attempts to link a matching visit plan if one exists.
        """
        self.ensure_one()
        today = fields.Date.today()

        tracker = self.env['salesperson.tracker'].search([
            ('user_id', '=', self.id),
            ('visit_date', '=', today),
        ], limit=1)

        if tracker:
            _logger.debug(
                "_ensure_salesperson_tracker: found existing tracker id=%s for user=%s date=%s",
                tracker.id, self.name, today,
            )
            return tracker

        # Create a new tracker for today
        tracker = self.env['salesperson.tracker'].create({
            'user_id': self.id,
            'visit_date': today,
            'state': 'planned',
        })
        _logger.info(
            "_ensure_salesperson_tracker: created new tracker id=%s for user=%s date=%s",
            tracker.id, self.name, today,
        )

        # Link a plan for today if one exists
        plan = self.env['salesperson.visit.plan'].search([
            ('user_id', '=', self.id),
            ('visit_date', '=', today),
        ], limit=1)
        if plan:
            tracker.plan_id = plan.id
            tracker.action_load_target_lines()
            _logger.info(
                "_ensure_salesperson_tracker: linked plan id=%s to tracker id=%s for user=%s",
                plan.id, tracker.id, self.name,
            )
        else:
            _logger.debug(
                "_ensure_salesperson_tracker: no plan found for user=%s date=%s",
                self.name, today,
            )

        return tracker
