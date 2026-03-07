"""
Professional Member Card Generator
Creates physical and digital membership cards
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
        self.card_bg_color = (255, 255, 255)
        self.primary_color = (0, 102, 204)  # OOU Blue
        self.secondary_color = (255, 215, 0)  # Gold
        self.text_color = (33, 37, 41)
        
        # Ensure directories exist
        os.makedirs('static/cards', exist_ok=True)
        os.makedirs('static/uploads/member-photos', exist_ok=True)
    
    def generate_member_card(self, member_data):
        """
        Generate a professional membership card
        
        member_data = {
            'id': 1,
            'member_number': 'OOU/2024/001',
            'full_name': 'John Doe',
            'photo_path': 'path/to/photo.jpg',
            'join_date': '2024-01-15',
            'membership_type': 'Full Member',
            'qr_data': 'member_data_for_scanning'
        }
        """
        
        # Create blank card
        card = Image.new('RGB', (self.card_width, self.card_height), self.card_bg_color)
        draw = ImageDraw.Draw(card)
        
        # Add background pattern/gradient
        self._add_background_gradient(card, draw)
        
        # Add university/cooperative logo
        self._add_logo(card)
        
        # Add member photo
        if member_data.get('photo_path') and os.path.exists(member_data['photo_path']):
            self._add_photo(card, member_data['photo_path'])
        else:
            self._add_default_avatar(card)
        
        # Add member details
        self._add_member_details(card, draw, member_data)
        
        # Add QR code
        self._add_qr_code(card, member_data['qr_data'])
        
        # Add barcode
        self._add_barcode(card, member_data['member_number'])
        
        # Add security features
        self._add_security_features(card, draw)
        
        # Save card
        card_filename = f"member_card_{member_data['member_number']}.png"
        card_path = os.path.join('static/cards', card_filename)
        card.save(card_path, 'PNG', dpi=(300, 300))
        
        return card_path
    
    def _add_background_gradient(self, card, draw):
        """Add professional gradient background"""
        for i in range(self.card_height):
            # Light gradient from top to bottom
            color_value = int(245 - (i * 0.1))
            draw.line([(0, i), (self.card_width, i)], 
                     fill=(color_value, color_value, color_value))
        
        # Add diagonal stripe
        draw.polygon([(0, 0), (200, 0), (0, 150)], 
                    fill=self.primary_color)
    
    def _add_logo(self, card):
        """Add cooperative logo"""
        try:
            logo = Image.open('static/images/logo.png')
            logo = logo.resize((100, 100))
            card.paste(logo, (30, 30), logo if logo.mode == 'RGBA' else None)
        except:
            # Create text logo if image not found
            draw = ImageDraw.Draw(card)
            try:
                font = ImageFont.truetype("arial.ttf", 40)
            except:
                font = ImageFont.load_default()
            draw.text((30, 30), "OOU Coop", fill=self.primary_color, font=font)
    
    def _add_photo(self, card, photo_path):
        """Add member photograph"""
        try:
            photo = Image.open(photo_path)
            photo = photo.resize((150, 150))
            
            # Create circular mask
            mask = Image.new('L', (150, 150), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, 150, 150), fill=255)
            
            # Apply mask
            card.paste(photo, (600, 80), mask)
            
            # Add border
            draw = ImageDraw.Draw(card)
            draw.ellipse((600, 80, 750, 230), outline=self.secondary_color, width=3)
        except:
            self._add_default_avatar(card)
    
    def _add_default_avatar(self, card):
        """Add default avatar if no photo"""
        draw = ImageDraw.Draw(card)
        draw.ellipse((600, 80, 750, 230), outline=self.primary_color, width=2)
        draw.text((660, 140), "📷", fill=self.primary_color)
    
    def _add_member_details(self, card, draw, member_data):
        """Add member information to card"""
        try:
            # Try to load professional fonts
            title_font = ImageFont.truetype("arialbd.ttf", 32)
            name_font = ImageFont.truetype("arialbd.ttf", 28)
            text_font = ImageFont.truetype("arial.ttf", 20)
            small_font = ImageFont.truetype("arial.ttf", 16)
        except:
            title_font = ImageFont.load_default()
            name_font = ImageFont.load_default()
            text_font = ImageFont.load_default()
            small_font = ImageFont.load_default()
        
        # Member number
        draw.text((80, 200), "Member No:", fill=self.primary_color, font=text_font)
        draw.text((200, 200), member_data['member_number'], 
                 fill=self.text_color, font=name_font)
        
        # Full name
        draw.text((80, 250), "Name:", fill=self.primary_color, font=text_font)
        draw.text((200, 250), member_data['full_name'][:30], 
                 fill=self.text_color, font=name_font)
        
        # Join date
        draw.text((80, 300), "Joined:", fill=self.primary_color, font=text_font)
        draw.text((200, 300), member_data['join_date'], 
                 fill=self.text_color, font=text_font)
        
        # Membership type
        draw.text((80, 350), "Type:", fill=self.primary_color, font=text_font)
        draw.text((200, 350), member_data['membership_type'], 
                 fill=self.secondary_color, font=text_font)
        
        # Card expiry
        expiry = datetime.now().replace(year=datetime.now().year + 1).strftime('%Y-%m-%d')
        draw.text((80, 400), "Expires:", fill=self.primary_color, font=small_font)
        draw.text((200, 400), expiry, fill=(255, 0, 0), font=small_font)
    
    def _add_qr_code(self, card, data):
        """Add QR code for quick access"""
        qr = qrcode.QRCode(
            version=1,
            box_size=5,
            border=2
        )
        qr.add_data(data)
        qr.make(fit=True)
        
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_img = qr_img.resize((100, 100))
        
        card.paste(qr_img, (750, 400))
    
    def _add_barcode(self, card, member_number):
        """Add barcode for scanning"""
        import barcode
        from barcode.writer import ImageWriter
        
        try:
            # Generate barcode
            code128 = barcode.get('code128', member_number, writer=ImageWriter())
            barcode_path = f"static/temp/barcode_{member_number}"
            code128.save(barcode_path)
            
            # Load and resize barcode
            barcode_img = Image.open(f"{barcode_path}.png")
            barcode_img = barcode_img.resize((200, 50))
            
            # Add to card
            card.paste(barcode_img, (80, 450))
            
            # Cleanup
            os.remove(f"{barcode_path}.png")
        except:
            # Fallback to text barcode
            draw = ImageDraw.Draw(card)
            draw.text((80, 460), f"||| {member_number} |||", fill=self.text_color)
    
    def _add_security_features(self, card, draw):
        """Add security features to prevent forgery"""
        # Micro text
        try:
            micro_font = ImageFont.truetype("arial.ttf", 8)
        except:
            micro_font = ImageFont.load_default()
        
        micro_text = "OOU ACCTG 2005 ALUMNI COOPERATIVE " * 10
        draw.text((30, 520), micro_text[:200], fill=(200, 200, 200), font=micro_font)
        
        # Holographic effect pattern (simulated)
        for i in range(0, self.card_width, 50):
            draw.line([(i, 0), (i+25, self.card_height)], 
                     fill=(240, 240, 240), width=1)
    
    def generate_digital_card(self, member_data):
        """Generate digital card for mobile wallet"""
        # Create smaller version for mobile
        card = self.generate_member_card(member_data)
        
        # Add to mobile wallet format (Apple Wallet/Google Pay)
        # This would integrate with pass generation APIs
        
        return card
    
    def print_card(self, card_path, copies=1):
        """Send card to printer"""
        import subprocess
        import platform
        
        if platform.system() == 'Windows':
            # Windows printing
            subprocess.run(['mspaint', '/pt', card_path])
        elif platform.system() == 'Linux':
            subprocess.run(['lp', '-n', str(copies), card_path])
        elif platform.system() == 'Darwin':  # macOS
            subprocess.run(['lpr', '-#', str(copies), card_path])

# Batch card generator
class BatchCardGenerator:
    def __init__(self):
        self.generator = MemberCardGenerator()
    
    def generate_all_member_cards(self, members):
        """Generate cards for all members"""
        cards = []
        for member in members:
            card_path = self.generator.generate_member_card(member)
            cards.append(card_path)
        return cards
    
    def generate_cards_for_printing(self, members):
        """Generate cards optimized for printing"""
        # Arrange multiple cards on a sheet for printing
        sheet = Image.new('RGB', (2480, 3508), 'white')  # A4 size
        
        x, y = 100, 100
        cards_per_row = 2
        
        for i, member in enumerate(members):
            card_path = self.generator.generate_member_card(member)
            card = Image.open(card_path)
            card = card.resize((1100, 660))
            
            sheet.paste(card, (x, y))
            
            x += 1200
            if (i + 1) % cards_per_row == 0:
                x = 100
                y += 800
        
        sheet.save('static/cards/print_sheet.pdf', 'PDF', resolution=300)
        return 'static/cards/print_sheet.pdf'