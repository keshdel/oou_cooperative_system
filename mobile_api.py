"""
REST API for Mobile App Integration
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required
import jwt
import datetime

mobile_api = Blueprint('mobile_api', __name__)

@mobile_api.route('/api/mobile/login', methods=['POST'])
def mobile_login():
    """Mobile app authentication"""
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    # Authenticate user
    user = authenticate_user(username, password)
    
    if user:
        # Generate JWT token
        token = jwt.encode({
            'user_id': user.id,
            'exp': datetime.datetime.utcnow() + datetime.timedelta(days=7)
        }, app.config['SECRET_KEY'])
        
        return jsonify({
            'success': True,
            'token': token,
            'user': {
                'id': user.id,
                'name': user.full_name,
                'email': user.email,
                'role': user.role
            }
        })
    
    return jsonify({'success': False, 'error': 'Invalid credentials'}), 401

@mobile_api.route('/api/mobile/dashboard')
@login_required
def mobile_dashboard():
    """Get dashboard data for mobile"""
    user_id = current_user.id
    
    # Get user data
    data = {
        'savings': get_user_savings(user_id),
        'loans': get_user_loans(user_id),
        'recent_transactions': get_transactions(user_id, limit=10),
        'notifications': get_notifications(user_id),
        'card': get_digital_card(user_id)
    }
    
    return jsonify(data)

@mobile_api.route('/api/mobile/card/<int:user_id>')
def get_mobile_card(user_id):
    """Get digital card for mobile display"""
    card_data = generate_mobile_card(user_id)
    return jsonify(card_data)

@mobile_api.route('/api/mobile/pay', methods=['POST'])
@login_required
def mobile_payment():
    """Process payment from mobile"""
    data = request.json
    result = process_payment(
        user_id=current_user.id,
        amount=data['amount'],
        loan_id=data.get('loan_id'),
        method=data['method']
    )
    
    return jsonify(result)