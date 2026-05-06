from collections import defaultdict
from datetime import datetime
from math import asin, cos, radians, sin, sqrt
import pytz
from odoo import api, fields, models

def _haversine_distance_meters(lat1, lon1, lat2, lon2):
    radius = 6371000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2.0) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2.0) ** 2
    return 2.0 * radius * asin(sqrt(a))


class SalespersonVisitPlan(models.Model):
    _name = "salesperson.visit.plan"
    _description = "Salesperson Planned Visit"
    _order = "visit_date desc, sequence, id"
    _inherit = ['mail.thread', 'mail.activity.mixin']


    name = fields.Char(default="New", copy=False, tracking=True)
    date = fields.Date(default=fields.Date.today)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    user_id = fields.Many2one("res.users", required=True, string="Sales person", ondelete="cascade")
    company_id = fields.Many2one("res.company", related="user_id.company_id", store=True)
    sale_team_id = fields.Many2one("crm.team", related="user_id.sale_team_id", store=True)
    tracker_id = fields.Many2one(
    "salesperson.tracker",
    string="Tracker"
     )
    visit_date = fields.Date(required=True, tracking=True, default=fields.Date.context_today)

    partner_ids = fields.Many2many(
        comodel_name="res.partner",
        relation="salesperson_visit_plan_partner_rel",
        column1="plan_id",
        column2="partner_id",
        string="Customers",
    )

    location_name = fields.Char(default="New Location")
    manual_latitude = fields.Float(digits=(16, 7))
    manual_longitude = fields.Float(digits=(16, 7))
    latitude = fields.Float(store=True, digits=(16, 7))
    longitude = fields.Float(store=True, digits=(16, 7))
    radius_meters = fields.Float(default=100.0)
    openstreetmap_url = fields.Char()
    
    checkin_time = fields.Datetime()
    checkout_time = fields.Datetime()

    stay_minutes = fields.Float(compute='_compute_stay', store=True)

    is_covered = fields.Boolean(
        compute="_compute_is_covered",
        store=True
    )

    coverage_color = fields.Integer(compute="_compute_coverage_color")

 
    expense_transport = fields.Float()
    expense_food = fields.Float()
    expense_other = fields.Float()

    total_expense = fields.Float(
        compute='_compute_total_expense',
        store=True
    )

  
    next_followup_date = fields.Date()
    html_note = fields.Html(string="HTML Note", sanitize=True)

    manager_notes = fields.Text()

    priority = fields.Selection([
        ("0", "Normal"),
        ("1", "High"),
        ("2", "Urgent")
    ], default="0")

    purpose = fields.Selection([
        ('order', 'New Order'),
        ('payment', 'Payment Collection'),
        ('complaint', 'Complaint'),
        ('followup', 'Follow Up'),
        ('demo', 'Product Demo')
    ], default='followup', tracking=True)

    is_manager = fields.Boolean(
        related="user_id.is_manager",
        store=False
    )

    state = fields.Selection([
        ("draft", "Draft"),
        ("submitted", "Submitted"),
        ("accepted", "Accepted"),
        ("rejected", "Rejected"),
        ("done", "Done")
    ], default="draft", tracking=True)

    space_line_ids = fields.One2many(
        "add.space.for.salesperson.line",
        "plan_id",
        string="Space Lines"
    )
    
    stay_duration_display = fields.Char(
        compute="_compute_stay_duration_display",
        store=False
    )

    def _compute_stay_duration_display(self):
        for rec in self:
            if rec.checkin_time and rec.checkout_time:
                diff = rec.checkout_time - rec.checkin_time
                minutes = diff.total_seconds() / 60
                rec.stay_duration_display = f"{int(minutes)} min"
            else:
                rec.stay_duration_display = "0 min"

    @api.depends('checkin_time', 'checkout_time')
    def _compute_stay(self):
        for rec in self:
            if rec.checkin_time and rec.checkout_time:
                diff = rec.checkout_time - rec.checkin_time
                rec.stay_minutes = (diff.total_seconds() / 60) 
            else:
                rec.stay_minutes = 0

    @api.depends('checkin_time', 'checkout_time')
    def _compute_is_covered(self):
        for rec in self:
            rec.is_covered = bool(rec.checkin_time and rec.checkout_time)

    def _compute_coverage_color(self):
        for rec in self:
            rec.coverage_color = 10 if rec.is_covered else 1

    @api.depends('expense_transport', 'expense_food', 'expense_other')
    def _compute_total_expense(self):
        for rec in self:
            rec.total_expense = (
                rec.expense_transport +
                rec.expense_food +
                rec.expense_other
            )

    def action_submit(self):
        
        self.filtered(lambda r: r.state == 'draft').write({'state': 'submitted'})
        self._push_to_dashboard()

    def action_reset_draft(self):
        self.write({'state': 'draft'})

    def action_accept(self):
       self.state = "accepted"

    

    def action_open_moving_map_view(self):
        self.ensure_one()
        tracker = self.env["salesperson.tracker"].sudo().search(
            [("user_id", "=", self.user_id.id)], limit=1
        )
        if not tracker:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Tracker Not Found",
                    "message": "No active tracker found for this salesperson.",
                    "type": "warning",
                    "sticky": False,
                },
            }
        return {
            "type": "ir.actions.act_url",
            "url": "/salesperson_tracking/moving_map/%d" % tracker.id,
            "target": "new",
        }

    def action_open_live_tracking_page(self):
        self.ensure_one()
        return self.user_id.action_open_live_tracking_page()


    def _push_to_dashboard(self):
        salesperson_tracker = self.env["salesperson.tracker"]
        Line = self.env["sales.person.space.line"]

        for rec in self:
            tracker = salesperson_tracker.search([
                ("plan_id", "=", rec.id)
            ], limit=1)

            if not tracker:
                tracker = salesperson_tracker.create({
                    "user_id": rec.user_id.id,
                    "sales_person": rec.user_id.name,
                    "manager": rec.user_id.parent_id.name if rec.user_id.parent_id else False,
                    "visit_date": rec.visit_date,
                    "location_name": rec.location_name,
                    "state": 'planned',
                    "expense_transport": rec.expense_transport,
                    "expense_food": rec.expense_food,
                    "expense_other": rec.expense_other,
                    "plan_id": rec.id,
                    "checkin_time": rec.checkin_time,
                    "checkout_time": rec.checkout_time,
                })
            else:
                tracker.write({
                    "location_name": rec.location_name,
                    "expense_transport": rec.expense_transport,
                    "expense_food": rec.expense_food,
                    "expense_other": rec.expense_other,
                })

            rec.tracker_id = tracker.id

            tracker.write({
                "partner_ids": [(6, 0, rec.partner_ids.ids)]
            })

            # পরিষ্কার করে আবার create
            tracker.line_ids.unlink()

            for space_line in rec.space_line_ids:
                Line.create({
                    "salesperson_tracker_id": tracker.id,
                    "plan_id": rec.id,
                    "partner_id": space_line.partner_id.id,
                    "visit_date": space_line.visit_date,
                    "from_location": space_line.from_location,
                    "to_location": space_line.to_location,
                    "total_cost": space_line.total_cost,
                    "notes": space_line.notes or "",
                })
  
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                today_str = datetime.today().strftime("%d-%m-%Y")
                last = self.search([], order="id desc", limit=1)
                next_seq = 1
                if last:
                    try:
                        next_seq = int(last.name.split("-")[-1]) + 1
                    except Exception:
                        next_seq = 1
                vals["name"] = f"{today_str}-{str(next_seq).zfill(5)}"
        return super().create(vals_list)



class AddSpaceForSalespersonLine(models.Model):
    _name = 'add.space.for.salesperson.line'
    _description = 'Visit Line'
    _order = 'sequence,id'

    plan_id = fields.Many2one('salesperson.visit.plan', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    partner_id = fields.Many2one('res.partner', required=True)
    visit_date = fields.Date(required=True)
    from_location = fields.Char()
    to_location = fields.Char()
    total_cost = fields.Float()
    notes = fields.Text()
    status = fields.Selection(related='plan_id.state', store=True)