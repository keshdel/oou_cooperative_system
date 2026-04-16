"""
Professional Member Card Generator – Enhanced Design
"""

import qrcode
from PIL import Image, ImageDraw, ImageFont
import os
from datetime import datetime
import uuid

class MemberCardGenerator:
    def __init__(self):
        self.card_width = 900
        self.card_height = 540
        self.bg_color = (255, 255, 255)
        self.primary_color = (0, 102, 204)      # OOU Blue
        self.secondary_color = (255, 215, 0)    # Gold
        self.text_color = (33, 37, 41)
        self.light_text = (100, 100, 100)
        
        # Ensure directories exist
        os.makedirs('static/cards', exist_ok=True)
        os.makedirs('static/uploads/member-photos', exist_ok=True)

    def generate_member_card(self, member_data):
        """
        Generate a professional membership card.
        member_data keys: member_number, full_name, join_date, membership_type,
                          photo_path (optional), qr_data (verification URL)
        """
        # Create blank card
        card = Image.new('RGB', (self.card_width, self.card_height), self.bg_color)
        draw = ImageDraw.Draw(card)

        # Draw background gradient
        self._draw_gradient_background(card, draw)

        # Draw decorative header bar
        draw.rectangle([(0, 0), (self.card_width, 110)], fill=self.primary_color)
        draw.rectangle([(0, 110), (self.card_width, 115)], fill=self.secondary_color)

        # Add cooperative name
        try:
            font_title = ImageFont.truetype("arialbd.ttf", 32)
            font_sub = ImageFont.truetype("arial.ttf", 16)
        except:
            font_title = ImageFont.load_default()
            font_sub = ImageFont.load_default()

        draw.text((30, 35), "OOU Acctg 2005 Alumni CMS", fill=(255,255,255), font=font_title)
        draw.text((30, 75), "Cooperative Multipurpose Society", fill=(255,255,255), font=font_sub)

        # Add member photo (if exists)
        photo_position = self._add_photo(card, member_data.get('photo_path'))

        # Add member details
        self._add_member_details(card, draw, member_data, photo_position)

        # Add QR code (bottom right)
        self._add_qr_code(card, member_data['qr_data'])

        # Add barcode (bottom left)
        self._add_barcode(card, member_data['member_number'])

        # Add security watermark
        self._add_watermark(card, draw)

        # Save card
        safe_number = member_data['member_number'].replace('/', '_').replace('\\', '_')
        filename = f"member_card_{safe_number}.png"
        card_path = os.path.join('static/cards', filename)
        card.save(card_path, 'PNG')
        return card_path

    def _draw_gradient_background(self, card, draw):
        """Draw a subtle gradient background."""
        width, height = card.size
        for i in range(height):
            # Light blue to white gradient
            r = 240 - int(i * 0.05)
            g = 245 - int(i * 0.05)
            b = 255 - int(i * 0.03)
            draw.line([(0, i), (width, i)], fill=(r, g, b))

    def _add_photo(self, card, photo_path):
        """Place member photo (circular)."""
        x, y = 650, 140
        size = 150
        if photo_path and os.path.exists(photo_path):
            try:
                photo = Image.open(photo_path).resize((size, size), Image.LANCZOS)
                # Create circular mask
                mask = Image.new('L', (size, size), 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.ellipse((0, 0, size, size), fill=255)
                card.paste(photo, (x, y), mask)
                # Add gold border
                draw = ImageDraw.Draw(card)
                draw.ellipse((x, y, x+size, y+size), outline=self.secondary_color, width=4)
            except:
                self._draw_default_avatar(card, x, y, size)
        else:
            self._draw_default_avatar(card, x, y, size)
        return (x, y, size)

    def _draw_default_avatar(self, card, x, y, size):
        """Draw a placeholder avatar."""
        draw = ImageDraw.Draw(card)
        draw.ellipse((x, y, x+size, y+size), outline=self.primary_color, width=2)
        draw.text((x+60, y+60), "📷", fill=self.primary_color)

    def _add_member_details(self, card, draw, member_data, photo_pos):
        """Add formatted member information."""
        try:
            label_font = ImageFont.truetype("arialbd.ttf", 18)
            value_font = ImageFont.truetype("arial.ttf", 22)
            small_font = ImageFont.truetype("arial.ttf", 14)
        except:
            label_font = ImageFont.load_default()
            value_font = ImageFont.load_default()
            small_font = ImageFont.load_default()

        # Left column (text details)
        x_start = 50
        y_start = 150
        line_height = 45

        # Member number
        draw.text((x_start, y_start), "Member Number", fill=self.light_text, font=label_font)
        draw.text((x_start, y_start+25), member_data['member_number'], fill=self.primary_color, font=value_font)

        # Full name
        draw.text((x_start, y_start+line_height), "Full Name", fill=self.light_text, font=label_font)
        draw.text((x_start, y_start+line_height+25), member_data['full_name'][:35], fill=self.text_color, font=value_font)

        # Join date
        draw.text((x_start, y_start+line_height*2), "Member Since", fill=self.light_text, font=label_font)
        draw.text((x_start, y_start+line_height*2+25), member_data['join_date'], fill=self.text_color, font=value_font)

        # Membership type
        draw.text((x_start, y_start+line_height*3), "Membership Type", fill=self.light_text, font=label_font)
        draw.text((x_start, y_start+line_height*3+25), member_data['membership_type'], fill=self.secondary_color, font=value_font)

        # Expiry date
        expiry = (datetime.now().replace(year=datetime.now().year + 1)).strftime('%Y-%m-%d')
        draw.text((x_start, y_start+line_height*4), "Valid Until", fill=self.light_text, font=label_font)
        draw.text((x_start, y_start+line_height*4+25), expiry, fill=(200, 0, 0), font=value_font)

        # Small footer text
        draw.text((50, 500), "This card is the property of OOU Cooperative. If found, please return to any branch.",
                  fill=self.light_text, font=small_font)

    def _add_qr_code(self, card, data):
        """Add QR code at bottom right."""
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(data)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").resize((120, 120))
        card.paste(qr_img, (self.card_width - 150, self.card_height - 150))

    def _add_barcode(self, card, member_number):
        """Add barcode (simulated with text if barcode lib not available)."""
        try:
            import barcode
            from barcode.writer import ImageWriter
            code128 = barcode.get('code128', member_number, writer=ImageWriter())
            temp_path = "static/temp/barcode_temp"
            code128.save(temp_path)
            barcode_img = Image.open(f"{temp_path}.png").resize((250, 60))
            card.paste(barcode_img, (50, self.card_height - 80))
            os.remove(f"{temp_path}.png")
        except:
            draw = ImageDraw.Draw(card)
            draw.text((50, self.card_height - 70), member_number, fill=self.text_color)

    def _add_watermark(self, card, draw):
        """Add a subtle watermark (cooperative name repeated)."""
        try:
            font = ImageFont.truetype("arial.ttf", 30)
        except:
            font = ImageFont.load_default()
        draw.text((200, 280), "OOU COOPERATIVE", fill=(230, 230, 230), font=font)