import base64
from datetime import timedelta
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

class SalespersonCheckin(models.Model):
    
    _name = "salesperson.checkin"
    _description = "Salesperson Check-In / Check-Out"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "checkin_time desc, id desc"
    _rec_name ="user_id"
    
    name = fields.Char(string="Visit Reference", required=True, default="New Check-In", copy=False, tracking=True)
    tracker_id = fields.Many2one("salesperson.tracker", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one("res.users", related="tracker_id.user_id", store=True, readonly=True, index=True)
    company_id = fields.Many2one("res.company", related="tracker_id.company_id", store=True, readonly=True)
    visit_plan_id = fields.Many2one("salesperson.visit.plan", string="Visit Plan", ondelete="set null")
    location_name = fields.Char(string="Location Name", required=True)
    checkin_latitude = fields.Float(string="Check-In Latitude", digits=(16, 7))
    checkin_longitude = fields.Float(string="Check-In Longitude", digits=(16, 7))
    checkout_latitude = fields.Float(string="Check-Out Latitude", digits=(16, 7))
    checkout_longitude = fields.Float(string="Check-Out Longitude", digits=(16, 7))
    checkin_time = fields.Datetime(string="Check-In Time", required=True, default=fields.Datetime.now, tracking=True)
    checkout_time = fields.Datetime(string="Check-Out Time", tracking=True)
    duration_minutes = fields.Float(string="Duration (min)", compute="_compute_duration", store=True)
    duration_display = fields.Char(string="Time Spent", compute="_compute_duration", store=True)

    state = fields.Selection(
        [("checked_in", "Checked In"), ("checked_out", "Checked Out")],
        default="checked_in",
        tracking=True,
    )

    selfie_image = fields.Binary(string="Selfie Proof", attachment=True)
    selfie_filename = fields.Char(string="Selfie Filename")
    selfie_latitude = fields.Float(string="Selfie Latitude", digits=(16, 7))
    selfie_longitude = fields.Float(string="Selfie Longitude", digits=(16, 7))
    selfie_taken_at = fields.Datetime(string="Selfie Taken At")

    notes = fields.Text(string="Activity Notes")
    meeting_outcome = fields.Selection(
        [
            ("positive", "Positive"),
            ("neutral", "Neutral"),
            ("negative", "Negative"),
            ("followup_needed", "Follow-up Needed"),
            ("deal_closed", "Deal Closed"),
        ],
        string="Meeting Outcome",
        tracking=True,
    )
    customer_feedback = fields.Text(string="Customer Feedback")

    partner_id = fields.Many2one("res.partner", string="Customer / Partner")
    crm_lead_id = fields.Many2one("crm.lead", string="CRM Opportunity / Lead")
    sale_order_id = fields.Many2one("sale.order", string="Sale Order")
    
    checkin_map_url = fields.Char(compute="_compute_map_urls")
    checkout_map_url = fields.Char(compute="_compute_map_urls")

    @api.depends("checkin_time", "checkout_time")
    def _compute_duration(self):
        for rec in self:
            if rec.checkin_time and rec.checkout_time:
                delta = rec.checkout_time - rec.checkin_time
                mins = max(delta.total_seconds() / 60.0, 0)
                rec.duration_minutes = mins
                hours, m = divmod(int(mins), 60)
                rec.duration_display = "%sh %sm" % (hours, m) if hours else "%sm" % m
            else:
                rec.duration_minutes = 0.0
                rec.duration_display = "--"

    @api.depends("checkin_latitude", "checkin_longitude", "checkout_latitude", "checkout_longitude")
    def _compute_map_urls(self):
        for rec in self:
            if rec.checkin_latitude or rec.checkin_longitude:
                rec.checkin_map_url = "https://www.openstreetmap.org/?mlat=%s&mlon=%s#map=16/%s/%s" % (
                    rec.checkin_latitude, rec.checkin_longitude,
                    rec.checkin_latitude, rec.checkin_longitude,
                )
            else:
                rec.checkin_map_url = False
            if rec.checkout_latitude or rec.checkout_longitude:
                rec.checkout_map_url = "https://www.openstreetmap.org/?mlat=%s&mlon=%s#map=16/%s/%s" % (
                    rec.checkout_latitude, rec.checkout_longitude,
                    rec.checkout_latitude, rec.checkout_longitude,
                )
            else:
                rec.checkout_map_url = False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New Check-In") == "New Check-In":
                vals["name"] = self.env["ir.sequence"].next_by_code("salesperson.checkin") or "New Check-In"
        return super().create(vals_list)

    def action_checkout(self, latitude=None, longitude=None):
        self.ensure_one()
        if self.state == "checked_out":
            raise ValidationError(_("Already checked out."))
        vals = {
            "state": "checked_out",
            "checkout_time": fields.Datetime.now(),
        }
        if latitude:
            vals["checkout_latitude"] = latitude
        if longitude:
            vals["checkout_longitude"] = longitude
        self.write(vals)
        
        self.env["salesperson.kpi"]._refresh_today(self.user_id)
        return True

    def action_link_crm(self):
        """Open wizard to link to CRM lead."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Link to CRM"),
            "res_model": "crm.lead",
            "view_mode": "list,form",
            "target": "new",
        }
