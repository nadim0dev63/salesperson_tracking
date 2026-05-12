from odoo import api, fields, models


class SalespersonLocationLog(models.Model):
    _name = "salesperson.location.log"
    _description = "Location History (every 2 seconds)"
    _order = "tracked_at desc"
    _auto_manage = True

    tracker_id = fields.Many2one("salesperson.tracker", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one("res.users", related="tracker_id.user_id", store=True, index=True)

    tracked_at = fields.Datetime(string="Timestamp", required=True, default=fields.Datetime.now, index=True)
    latitude = fields.Float(string="Latitude", required=True, digits=(16, 7))
    longitude = fields.Float(string="Longitude", required=True, digits=(16, 7))
    accuracy = fields.Float(string="Accuracy (m)", digits=(16, 2))
    speed = fields.Float(string="Speed (m/s)", digits=(16, 2))
    heading = fields.Float(string="Heading", digits=(16, 2))
    source = fields.Char(string="Source", default="browser")
    location_name = fields.Char(string="Location Name")

    openstreetmap_url = fields.Char(compute="_compute_map_url")

    @api.depends("latitude", "longitude")
    def _compute_map_url(self):
        for log in self:
            if log.latitude and log.longitude:
                log.openstreetmap_url = (
                    f"https://www.openstreetmap.org/?mlat={log.latitude}"
                    f"&mlon={log.longitude}#map=16/{log.latitude}/{log.longitude}"
                )