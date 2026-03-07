"""
Payment Gateway Integration
Supports Paystack, Flutterwave, Interswitch
"""

import requests
import hmac
import hashlib

class PaymentGateway:
    def __init__(self, gateway='paystack'):
        self.gateway = gateway
        
        if gateway == 'paystack':
            self.secret_key = 'YOUR_PAYSTACK_SECRET'
            self.public_key = 'YOUR_PAYSTACK_PUBLIC'
            self.base_url = 'https://api.paystack.co'
        
        elif gateway == 'flutterwave':
            self.secret_key = 'YOUR_FLUTTERWAVE_SECRET'
            self.public_key = 'YOUR_FLUTTERWAVE_PUBLIC'
            self.base_url = 'https://api.flutterwave.com/v3'
    
    def initialize_payment(self, email, amount, reference=None):
        """Initialize payment with gateway"""
        
        if self.gateway == 'paystack':
            return self._paystack_initialize(email, amount, reference)
        elif self.gateway == 'flutterwave':
            return self._flutterwave_initialize(email, amount, reference)
    
    def _paystack_initialize(self, email, amount, reference):
        headers = {
            'Authorization': f'Bearer {self.secret_key}',
            'Content-Type': 'application/json'
        }
        
        data = {
            'email': email,
            'amount': int(amount * 100),  # Paystack uses kobo
            'reference': reference or self.generate_reference()
        }
        
        response = requests.post(
            f'{self.base_url}/transaction/initialize',
            json=data,
            headers=headers
        )
        
        return response.json()
    
    def verify_payment(self, reference):
        """Verify payment status"""
        
        if self.gateway == 'paystack':
            return self._paystack_verify(reference)
    
    def _paystack_verify(self, reference):
        headers = {
            'Authorization': f'Bearer {self.secret_key}'
        }
        
        response = requests.get(
            f'{self.base_url}/transaction/verify/{reference}',
            headers=headers
        )
        
        return response.json()
    
    def webhook_handler(self, request):
        """Handle payment webhook"""
        # Verify signature
        signature = request.headers.get('x-paystack-signature')
        payload = request.get_data()
        
        computed = hmac.new(
            self.secret_key.encode(),
            payload,
            hashlib.sha512
        ).hexdigest()
        
        if signature != computed:
            return {'error': 'Invalid signature'}, 400
        
        # Process webhook
        event = request.json
        if event['event'] == 'charge.success':
            self.process_successful_payment(event['data'])
        
        return {'status': 'success'}, 200