from datetime import datetime
from odoo import api, fields, models, _


class SalespersonVisitPlan(models.Model):
    _name = "salesperson.visit.plan"
    _description = "Visit Plan"
    _order = "visit_date desc, id desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(string="Reference", required=True, copy=False, readonly=True, default=lambda self: _("New"))
    user_id = fields.Many2one("res.users", string="Salesman", required=True, tracking=True)
    company_id = fields.Many2one("res.company", related="user_id.company_id", store=True)
    sale_team_id = fields.Many2one("crm.team", related="user_id.sale_team_id", store=True)

    purpose = fields.Selection([
        ("order", "New Order"),
        ("payment", "Payment Collection"),
        ("complaint", "Complaint Resolution"),
        ("followup", "Follow Up"),
        ("demo", "Product Demo"),
    ], string="Purpose", required=True, default="followup", tracking=True)

    visit_date = fields.Date(string="Visit Date", required=True, default=fields.Date.context_today, tracking=True)
    priority = fields.Selection([
        ("0", "Normal"),
        ("1", "High"),
        ("2", "Urgent"),
    ], string="Priority", default="0", tracking=True)

    # Visit Plan Lines (Target Lines)
    target_line_ids = fields.One2many(
        "salesperson.visit.plan.line",
        "plan_id",
        string="Target Lines"
    )

    total_visits = fields.Integer(string="Total Visits", compute="_compute_total_visits", store=True)

    # Expenses
    transport_cost = fields.Float(string="Transport Cost", tracking=True)
    food_cost = fields.Float(string="Food Cost", tracking=True)
    other_cost = fields.Float(string="Other Cost", tracking=True)
    total_cost = fields.Float(string="Total Cost", compute="_compute_total_cost", store=True)

    # State
    state = fields.Selection([
        ("draft", "Draft"),
        ("submitted", "Submitted"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
    ], string="Status", default="draft", tracking=True)

    # Notes
    manager_notes = fields.Text(string="Manager Notes")
    internal_notes = fields.Html(string="Internal Notes")

    @api.depends("target_line_ids.customer_count")
    def _compute_total_visits(self):
        for plan in self:
            total = sum(line.customer_count for line in plan.target_line_ids)
            plan.total_visits = total

    @api.depends("transport_cost", "food_cost", "other_cost")
    def _compute_total_cost(self):
        for plan in self:
            plan.total_cost = plan.transport_cost + plan.food_cost + plan.other_cost

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                sequence = self.env["ir.sequence"].next_by_code("salesperson.visit.plan") or "/"
                vals["name"] = sequence
        return super().create(vals_list)

    def action_submit(self):
        self.filtered(lambda p: p.state == "draft").write({"state": "submitted"})

    def action_approve(self):
        self.filtered(lambda p: p.state == "submitted").write({"state": "approved"})

    def action_reject(self):
        self.filtered(lambda p: p.state == "submitted").write({"state": "rejected"})

    def action_reset_draft(self):
        self.write({"state": "draft"})

    def action_start_tracking(self):
        self.ensure_one()
        tracker = self.env["salesperson.tracker"].create({
            "user_id": self.user_id.id,
            "sales_person": self.user_id.name,
            "plan_id": self.id,
            "visit_date": self.visit_date,
            "purpose": self.purpose,
            "priority": self.priority,
            "transport_cost": self.transport_cost,
            "food_cost": self.food_cost,
            "other_cost": self.other_cost,
        })
        self.state = "in_progress"
        return tracker.action_open_live_tracking_page()

    def action_view_tracker(self):
        self.ensure_one()
        tracker = self.env["salesperson.tracker"].search([("plan_id", "=", self.id)], limit=1)
        if tracker:
            return {
                "type": "ir.actions.act_window",
                "res_model": "salesperson.tracker",
                "res_id": tracker.id,
                "view_mode": "form",
                "target": "current",
            }
        return True

    def action_print_visit_plan(self):
        return self.env.ref("zencore_salesperson_tracking.action_report_visit_plan").report_action(self)


class SalespersonVisitPlanLine(models.Model):
    _name = "salesperson.visit.plan.line"
    _description = "Visit Plan Target Line"

    plan_id = fields.Many2one("salesperson.visit.plan", string="Visit Plan", required=True, ondelete="cascade")

    expected_time_minutes = fields.Integer(string="Expected Time (min)")
    employee_ids = fields.Many2many("hr.employee", string="Accompanies")
    from_location = fields.Char(string="From Location")
    to_location = fields.Char(string="To Location")
    expected_km = fields.Float(string="Expected KM", digits=(16, 2))
    customer_ids = fields.Many2many("res.partner", string="Customers")
    customer_count = fields.Integer(string="Customer Count", compute="_compute_customer_count", store=True)
    notes = fields.Text(string="Notes")

    @api.depends("customer_ids")
    def _compute_customer_count(self):
        for line in self:
            line.customer_count = len(line.customer_ids)