from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    salesperson_tracker_id = fields.One2many(
        "salesperson.tracker",
        "user_id",
        string="Salesperson Tracker",
    )

    is_manager = fields.Boolean(string="Is Manager", default=False)
    is_salesperson = fields.Boolean(string="Is Salesperson", default=False)
    
    def _ensure_salesperson_tracker(self):
        self.ensure_one()
        tracker = self.env["salesperson.tracker"].sudo().search([("user_id", "=", self.id)], limit=1)
        if tracker:
            return tracker
        return self.env["salesperson.tracker"].sudo().create({"user_id": self.id})

    def action_open_live_tracking_page(self):
        self.ensure_one()
        self._ensure_salesperson_tracker()
        return {
            "type": "ir.actions.act_url",
            "url": "/salesperson_tracking/live",
            "target": "self",
        }
    
    