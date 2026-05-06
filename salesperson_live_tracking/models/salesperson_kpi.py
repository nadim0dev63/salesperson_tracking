from collections import defaultdict
from datetime import date, timedelta

from odoo import _, api, fields, models


class SalespersonKpi(models.Model):
  
    _name = "salesperson.kpi"
    _description = "Salesperson KPI Summary"
    
    date = fields.Datetime(string="Date")
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade", index=True)
    
    
   
   

    