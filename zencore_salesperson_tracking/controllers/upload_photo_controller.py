import base64
from odoo import http, fields
from odoo.http import request
from markupsafe import Markup


class SalespersonTrackingController(http.Controller):

    @http.route("/salesperson_tracking/save_photo", type="json", auth="user", methods=["POST"], csrf=False)
    def save_photo(self, image_data="", filename=None, latitude=None, longitude=None, location_name=None, **kwargs):
        try:
            if not image_data:
                return {"success": False, "message": "No image data received"}

            filename = filename or f"photo_{request.env.uid}_{fields.Datetime.now().timestamp()}.jpg"

            image_b64 = image_data.split(",", 1)[1] if "," in image_data else image_data

            tracker = request.env["salesperson.tracker"].sudo().search(
                [("user_id", "=", request.env.uid)], limit=1
            )
            if not tracker:
                return {"success": False, "message": "Tracker not found"}

            attachment = request.env["ir.attachment"].sudo().create({
                "name": filename,
                "type": "binary",
                "datas": image_b64,
                "mimetype": "image/jpeg",
                "res_model": "salesperson.tracker",
                "res_id": tracker.id,
                "description": f"Photo by {request.env.user.name}",
            })

            taken_at = fields.Datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if latitude and longitude:
                maps_url = f"https://www.openstreetmap.org/?mlat={latitude}&mlon={longitude}#map=16/{latitude}/{longitude}"
                place_label = location_name or f"{latitude}, {longitude}"
                location_line = Markup(
                    f'<br/><b>Location:</b> <a href="{maps_url}" target="_blank">{place_label}</a>'
                )
            else:
                location_line = Markup(f"<br/><b>Location:</b> {location_name or 'Unknown'}")

            tracker.message_post(
                body=Markup(
                    f"<b>Photo uploaded</b> by {request.env.user.name}<br/>"
                    f"<b>Taken at:</b> {taken_at}{location_line}"
                ),
                attachment_ids=[attachment.id],
            )

            # If there's an active check-in, attach photo to it
            active_checkin = request.env["salesperson.checkin"].sudo().search([
                ("tracker_id", "=", tracker.id),
                ("state", "=", "checked_in"),
            ], limit=1)
            if active_checkin and not active_checkin.selfie_image:
                active_checkin.write({
                    "selfie_image": image_b64,
                    "selfie_filename": filename,
                    "selfie_latitude": latitude,
                    "selfie_longitude": longitude,
                    "selfie_taken_at": fields.Datetime.now(),
                })

            return {
                "success": True,
                "attachment_id": attachment.id,
                "message": "Photo saved successfully",
            }

        except Exception as e:
            return {"success": False, "message": str(e)}