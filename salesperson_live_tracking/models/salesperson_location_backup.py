import json
import csv
import io
import base64
import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class SalespersonLocationBackup(models.Model):
    """
    7-day rolling backup of location logs.

    Every time the scheduled action fires it:
      1. Exports all SalespersonLocationLog records that are >= 7 days old
         into a compressed JSON snapshot stored here.
      2. Deletes those same records from the live log table.

    Each backup record covers exactly one salesperson (tracker_id) and one
    calendar day, so the table stays queryable without unpacking the JSON.
    """

    _name = "salesperson.location.backup"
    _description = "Salesperson Location Log Backup (7-day)"
    _order = "backup_date desc, id desc"

    tracker_id = fields.Many2one(
        "salesperson.tracker",
        required=True,
        ondelete="cascade",
        index=True,
        string="Salesperson",
    )
    user_id = fields.Many2one(
        "res.users",
        related="tracker_id.user_id",
        store=True,
        readonly=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="tracker_id.company_id",
        store=True,
        readonly=True,
    )

    backup_date = fields.Date(
        required=True,
        index=True,
        string="Backup Date",
        help="Calendar date the location records were originally captured on.",
    )
    record_count = fields.Integer(
        string="Records", readonly=True,
        help="Number of location log records captured in this backup.",
    )
    payload = fields.Text(
        string="JSON Payload",
        readonly=True,
        help="Serialised location records (list of dicts).",
    )
    created_at = fields.Datetime(
        string="Backed-up At",
        default=fields.Datetime.now,
        readonly=True,
        index=True,
    )
    retention_expires_at = fields.Datetime(
        string="Expires At",
        readonly=True,
        help="Backup will be auto-deleted after this timestamp (created_at + 7 days).",
    )

    # ------------------------------------------------------------------ #
    #  ORM                                                                 #
    # ------------------------------------------------------------------ #

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "retention_expires_at" not in vals:
                vals["retention_expires_at"] = (
                    fields.Datetime.now() + timedelta(days=7)
                )
        return super().create(vals_list)

    # ------------------------------------------------------------------ #
    #  Scheduled action — entry point                                      #
    # ------------------------------------------------------------------ #

    @api.model
    def action_run_data_policy(self):
        """
        Called by the ir.cron every 7 days.

        Steps
        -----
        1. Backup all location logs older than 7 days (grouped by
           tracker × calendar-day) into salesperson.location.backup.
        2. Delete those logs from salesperson.location.log.
        3. Delete backup records whose retention_expires_at has passed.
        """
        self._backup_old_location_logs()
        self._delete_expired_backups()

    # ------------------------------------------------------------------ #
    #  Step 1 – backup logs that are ≥ 7 days old                         #
    # ------------------------------------------------------------------ #

    def _backup_old_location_logs(self):
        LocationLog = self.env["salesperson.location.log"].sudo()
        cutoff = fields.Datetime.now() - timedelta(days=7)

        old_logs = LocationLog.search(
            [("tracked_at", "<=", cutoff)],
            order="tracker_id, tracked_at",
        )

        if not old_logs:
            _logger.info("Location data policy: no logs older than 7 days – nothing to backup.")
            return

        # Group by (tracker_id, date)
        grouped: dict[tuple, list] = {}
        for log in old_logs:
            day = log.tracked_at.date()
            key = (log.tracker_id.id, day)
            grouped.setdefault(key, []).append(log)

        _logger.info(
            "Location data policy: backing up %d logs across %d (tracker, day) groups.",
            len(old_logs),
            len(grouped),
        )

        backup_vals = []
        for (tracker_id, day), logs in grouped.items():
            records_data = [
                {
                    "id":            log.id,
                    "tracked_at":    log.tracked_at.isoformat(),
                    "latitude":      log.latitude,
                    "longitude":     log.longitude,
                    "accuracy":      log.accuracy,
                    "speed":         log.speed,
                    "heading":       log.heading,
                    "source":        log.source,
                    "location_name": log.location_name,
                }
                for log in logs
            ]
            backup_vals.append({
                "tracker_id":  tracker_id,
                "backup_date": day,
                "record_count": len(records_data),
                "payload":     json.dumps(records_data, ensure_ascii=False),
            })

        self.sudo().create(backup_vals)

        # Delete the now-backed-up logs from the live table
        old_logs.unlink()
        _logger.info("Location data policy: backup complete, %d live log records deleted.", len(old_logs))

    # ------------------------------------------------------------------ #
    #  Step 2 – delete backup records whose 7-day retention has expired   #
    # ------------------------------------------------------------------ #

    def _delete_expired_backups(self):
        now = fields.Datetime.now()
        expired = self.sudo().search([("retention_expires_at", "<=", now)])
        if not expired:
            _logger.info("Location data policy: no expired backups to delete.")
            return

        count = len(expired)
        expired.unlink()
        _logger.info("Location data policy: deleted %d expired backup records.", count)

    # ------------------------------------------------------------------ #
    #  Helper – restore a single backup record back to location.log       #
    # ------------------------------------------------------------------ #

    def action_download_csv(self):
        """Download this backup's location records as a CSV file."""
        self.ensure_one()
        if not self.payload:
            return

        records = json.loads(self.payload)

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            'tracked_at', 'latitude', 'longitude',
            'accuracy', 'speed', 'heading', 'source', 'location_name'
        ])
        writer.writeheader()
        for rec in records:
            writer.writerow({
                'tracked_at':    rec.get('tracked_at', ''),
                'latitude':      rec.get('latitude', ''),
                'longitude':     rec.get('longitude', ''),
                'accuracy':      rec.get('accuracy', ''),
                'speed':         rec.get('speed', ''),
                'heading':       rec.get('heading', ''),
                'source':        rec.get('source', ''),
                'location_name': rec.get('location_name', ''),
            })

        csv_bytes = output.getvalue().encode('utf-8')
        b64 = base64.b64encode(csv_bytes).decode('utf-8')
        filename = 'location_backup_%s_%s.csv' % (
            self.tracker_id.user_id.name or 'unknown',
            str(self.backup_date),
        )

        # Store as attachment and open download URL
        attachment = self.env['ir.attachment'].sudo().create({
            'name':      filename,
            'type':      'binary',
            'datas':     b64,
            'res_model': self._name,
            'res_id':    self.id,
            'mimetype':  'text/csv',
        })

        return {
            'type': 'ir.actions.act_url',
            'url':  '/web/content/%d?download=true' % attachment.id,
            'target': 'self',
        }

    def action_restore_backup(self):
        """
        Admin utility: push the JSON payload back into salesperson.location.log.
        """
        self.ensure_one()
        if not self.payload:
            return

        LocationLog = self.env["salesperson.location.log"].sudo()
        records = json.loads(self.payload)
        created = 0
        for rec in records:
            # Avoid duplicating if already restored
            existing = LocationLog.search([
                ("tracker_id", "=", self.tracker_id.id),
                ("tracked_at", "=", rec["tracked_at"]),
                ("latitude",   "=", rec["latitude"]),
                ("longitude",  "=", rec["longitude"]),
            ], limit=1)
            if not existing:
                LocationLog.create({
                    "tracker_id":    self.tracker_id.id,
                    "tracked_at":    rec["tracked_at"],
                    "latitude":      rec["latitude"],
                    "longitude":     rec["longitude"],
                    "accuracy":      rec.get("accuracy", 0.0),
                    "speed":         rec.get("speed", 0.0),
                    "heading":       rec.get("heading", 0.0),
                    "source":        rec.get("source", "backup"),
                    "location_name": rec.get("location_name"),
                })
                created += 1

        return {
            "type": "ir.actions.client",
            "tag":  "display_notification",
            "params": {
                "title":   _("Restore Complete"),
                "message": _("%d location records restored to live log.") % created,
                "type":    "success",
            },
        }
