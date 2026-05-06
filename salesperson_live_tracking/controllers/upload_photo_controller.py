import base64
from odoo import http, fields
from odoo.http import request
from markupsafe import Markup


class SalespersonTrackingController(http.Controller):

    @http.route('/salesperson_tracking/save_photo', type='json', auth='user', methods=['POST'], csrf=False)
    def save_photo(self, image_data='', filename=None,
                   latitude=None, longitude=None, location_name=None, **kwargs):
        try:
            if not image_data:
                return {'success': False, 'message': 'No image data received'}

            filename = filename or f'salesperson_photo_{request.env.uid}.jpg'

   
            image_b64 = image_data.split(',', 1)[1] if ',' in image_data else image_data

            tracker = request.env['salesperson.tracker'].sudo().search(
                [('user_id', '=', request.env.uid)], limit=1
            )
            if not tracker:
                return {'success': False, 'message': 'Tracker record not found for this user'}
            attachment = request.env['ir.attachment'].sudo().create({
                'name':        filename,
                'type':        'binary',
                'datas':       image_b64,
                'mimetype':    'image/jpeg',
                'res_model':   'salesperson.tracker',
                'res_id':      tracker.id,
                'description': f'Field photo — {request.env.user.name}',
            })

            taken_at = fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            if latitude and longitude:
                maps_url = (
                    f'https://www.openstreetmap.org/'
                    f'?mlat={latitude}&mlon={longitude}#map=16/{latitude}/{longitude}'
                )
                place_label = location_name or f'{latitude}, {longitude}'
                location_line = Markup(
                    f'<br/><b>Location:</b> '
                    f'<a href="{maps_url}" target="_blank">{place_label}</a>'
                    f' ({latitude}, {longitude})'
                )
            else:
                place_label   = location_name or 'Location unavailable'
                location_line = Markup(f'<br/><b>Location:</b> {place_label}')
                
            tracker.message_post(
                body=Markup(
                    f'<b>Field photo uploaded</b> by {request.env.user.name}<br/>'
                    f'<b>Taken at:</b> {taken_at}'
                    f'{location_line}'
                ),
                attachment_ids=[attachment.id],
            )

            return {
                'success':       True,
                'attachment_id': attachment.id,
                'location':      place_label,
                'message':       'Photo saved successfully',
            }

        except Exception as e:
            return {'success': False, 'message': str(e)}