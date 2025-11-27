from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, extract
from collections import defaultdict
from sqlalchemy import text, and_, or_
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps
from sqlalchemy import not_, or_, func
import hashlib
import os
import json
from types import SimpleNamespace
import base64
import io
# Giả định payment_services đã có sẵn và chứa MoMoPayment, VNPayPayment, ZaloPayPayment
from payment_services import MoMoPayment, VNPayPayment, ZaloPayPayment 
try:
    import qrcode
    QR_AVAILABLE = True
except Exception:
    QR_AVAILABLE = False


def generate_qr_base64(payload: str):
    """Return a base64 PNG string for the given payload, or None if failed/not available."""
    if not QR_AVAILABLE:
        return None
    try:
        # Tăng kích thước QR code (ví dụ: box_size=5, border=4)
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=5,
            border=4,
        )
        qr.add_data(payload)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return base64.b64encode(buf.getvalue()).decode('ascii')
    except Exception as e:
        print(f"QR generation failed: {e}")
        return None
        
def format_currency(value):
    """Định dạng tiền tệ theo chuẩn Việt Nam (VND)"""
    # Đảm bảo giá trị là số và xử lý None/0
    try:
        if value is None:
            value = 0
        # Định dạng số có dấu chấm phân cách hàng nghìn và thêm 'đ'
        return f'{float(value):,.0f}đ'.replace(',', '.')
    except:
        return '0đ' # Trả về 0đ nếu có lỗi chuyển đổi

app = Flask(__name__)
app.config['SECRET_KEY'] = 'muong-thanh-hotel-secret-key-2025'

app.config['SQLALCHEMY_DATABASE_URI'] = 'mssql+pyodbc://LYDUONG2004\\LY/muongthanh_hotel?driver=ODBC+Driver+17+for+SQL+Server&Trusted_Connection=yes'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ECHO'] = True

# Đăng ký filter format_currency vào môi trường Jinja2 (Đặt trước db = SQLAlchemy(app))
app.jinja_env.filters['format_currency'] = format_currency

db = SQLAlchemy(app)

# ===== DATABASE MODELS =====

class Review(db.Model):
    __tablename__ = 'reviews'
    __table_args__ = {'extend_existing': True} 
    
    id = db.Column(db.Integer, primary_key=True)
    
    # OK: rooms.id
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False) 
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False, unique=True)
    
    rating = db.Column(db.Integer, nullable=False) 
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    # Đã thêm cột status để khắc phục lỗi InvalidRequestError
    # Trạng thái: 'pending' (chờ duyệt), 'approved' (đã duyệt), 'rejected' (bị từ chối)
    status = db.Column(db.String(20), default='pending', nullable=False) 

    admin_reply = db.Column(db.Text) 
    reply_at = db.Column(db.DateTime) 
    
    room = db.relationship('Room', backref=db.backref('room_reviews', lazy=True))
    user = db.relationship('User', backref=db.backref('user_reviews', lazy=True)) 
    booking = db.relationship('Booking', backref=db.backref('review', uselist=False))
# ----- User Model -----
class User(db.Model):
    # Khai báo tên bảng rõ ràng
    __tablename__ = 'user' 
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(20))
    address = db.Column(db.String(255))
    role = db.Column(db.String(20), default='customer') # customer, admin, partner
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    # FIX: Xóa quan hệ reviews vì đã định nghĩa backref trong Review
    bookings = db.relationship('Booking', backref='user', lazy=True)
    # OLD: reviews = db.relationship('Review', backref='user', lazy=True)
    
    def __repr__(self):
        return f'<User {self.email}>'


# ----- Location Model -----
class Location(db.Model):
    __tablename__ = 'locations'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    city = db.Column(db.String(100))
    description = db.Column(db.Text)
    image = db.Column(db.String(255))
    
    # Relationships
    hotels = db.relationship('Hotel', backref='location', lazy=True)
    
    def __repr__(self):
        return f'<Location {self.name}>'


# ----- Hotel Model -----
class Hotel(db.Model):
    __tablename__ = 'hotels'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False)
    address = db.Column(db.String(255))
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    description = db.Column(db.Text)
    facilities = db.Column(db.Text)  # JSON string
    image = db.Column(db.String(255))
    rating = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    rooms = db.relationship('Room', backref='hotel', lazy=True)
    
    def __repr__(self):
        return f'<Hotel {self.name}>'


# ----- Room Model -----
class Room(db.Model):
    __tablename__ = 'rooms'
    
    id = db.Column(db.Integer, primary_key=True)
    hotel_id = db.Column(db.Integer, db.ForeignKey('hotels.id'), nullable=False)
    room_number = db.Column(db.String(20), nullable=False)
    room_type = db.Column(db.String(50), nullable=False)  # Standard, Deluxe, Suite, etc.
    price = db.Column(db.Float, nullable=False)
    max_people = db.Column(db.Integer, default=2)
    size = db.Column(db.Float)  # m2
    description = db.Column(db.Text)
    amenities = db.Column(db.Text)  # JSON string
    image = db.Column(db.String(255))
    status = db.Column(db.String(20), default='available')  # available, occupied, maintenance
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    floor = db.Column(db.Integer, default=1)
    # Relationships
    bookings = db.relationship('Booking', backref='room', lazy=True)
    # FIX: Xóa quan hệ reviews vì đã định nghĩa backref trong Review
    # OLD: reviews = db.relationship('Review', backref='room', lazy=True)
    
    def __repr__(self):
        return f'<Room {self.room_number} - {self.room_type}>'
    
    def is_available(self, check_in, check_out):
        """Kiểm tra phòng có available trong khoảng thời gian không"""
        overlapping_bookings = Booking.query.filter(
            Booking.room_id == self.id,
            Booking.status != 'cancelled',
            or_(
                and_(Booking.check_in <= check_in, Booking.check_out > check_in),
                and_(Booking.check_in < check_out, Booking.check_out >= check_out),
                and_(Booking.check_in >= check_in, Booking.check_out <= check_out)
            )
        ).all()
        return len(overlapping_bookings) == 0


# ----- Booking Model -----
class Booking(db.Model):
    __tablename__ = 'bookings'
    
    id = db.Column(db.Integer, primary_key=True)
    # FIX: Đã sửa từ 'users.id' thành 'user.id'
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # Nullable cho guest booking
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)
    
    # Guest info
    guest_name = db.Column(db.String(100), nullable=False)
    guest_phone = db.Column(db.String(20), nullable=False)
    guest_address = db.Column(db.String(200))
    
    # Booking details
    check_in = db.Column(db.DateTime, nullable=False)
    check_out = db.Column(db.DateTime, nullable=False)
    adults = db.Column(db.Integer, default=1)
    children = db.Column(db.Integer, default=0)
    total_price = db.Column(db.Float, nullable=False)
    
    # Payment
    payment_method = db.Column(db.String(50))  # momo, vnpay, zalopay, banking
    payment_status = db.Column(db.String(20), default='unpaid')  # unpaid, pending, paid, failed
    
    # Status
    status = db.Column(db.String(20), default="reserved") # pending, confirmed, cancelled, completed
    
    # Additional
    promotion_code = db.Column(db.String(50))
    special_requests = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    # FIX: Xóa quan hệ reviews vì đã định nghĩa backref trong Review
    # OLD: reviews = db.relationship('Review', backref='booking', lazy=True)
    
    def __repr__(self):
        return f'<Booking #{self.id} - Room {self.room_id}>'
    
    @property
    def nights(self):
        """Tính số đêm"""
        return (self.check_out - self.check_in).days


# ----- Promotion Model -----
class Promotion(db.Model):
    __tablename__ = 'promotions'
    
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.Text)
    discount_percent = db.Column(db.Float, nullable=False)
    min_amount = db.Column(db.Float, default=0)
    max_uses = db.Column(db.Integer)  # Null = unlimited
    current_uses = db.Column(db.Integer, default=0)
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=False)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Promotion {self.code} - {self.discount_percent}%>'
    
    def is_valid(self):
        """Kiểm tra mã có còn hợp lệ không"""
        now = datetime.now()
        if not self.active:
            return False
        if now < self.start_date or now > self.end_date:
            return False
        if self.max_uses and self.current_uses >= self.max_uses:
            return False
        return True


# ----- Service Model (Optional) -----
class Service(db.Model):
    __tablename__ = 'services'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Float, nullable=False)
    icon = db.Column(db.String(50))  # Font awesome icon class
    active = db.Column(db.Boolean, default=True)
    
    def __repr__(self):
        return f'<Service {self.name}>'


def init_db():
    """Khởi tạo database và thêm dữ liệu mẫu"""
    with app.app_context():
        # Tạo tất cả tables
        db.create_all() 
        
        # Kiểm tra đã có data chưa
        if User.query.first() is None:
            print("Đang khởi tạo dữ liệu mẫu...")
            
            # Tạo admin user
            admin = User(
                email='admin@muongthanh.com',
                password=generate_password_hash('admin123', method='pbkdf2:sha256'),
                full_name='Admin',
                phone='0123456789',
                role='admin'
            )
            db.session.add(admin)
            
            # Tạo customer user
            customer = User(
                email='customer@example.com',
                password=generate_password_hash('123456', method='pbkdf2:sha256'),
                full_name='Nguyễn Văn A',
                phone='0987654321',
                role='customer'
            )
            db.session.add(customer)
            
            # Tạo locations
            locations = [
                Location(name='Hà Nội', city='Hà Nội', description='Thủ đô ngàn năm văn hiến'),
                Location(name='Hồ Chí Minh', city='TP.HCM', description='Thành phố năng động'),
                Location(name='Đà Nẵng', city='Đà Nẵng', description='Thành phố đáng sống'),
            ]
            db.session.add_all(locations)
            db.session.commit()
            
            # Tạo hotels
            hotel1 = Hotel(
                name='Mường Thanh Grand Hà Nội',
                location_id=locations[0].id,
                address='40 Bà Triệu, Hoàn Kiếm, Hà Nội',
                phone='024-3946-2222',
                email='hanoi@muongthanh.com',
                description='Khách sạn 5 sao sang trọng',
                rating=4.5
            )
            hotel2 = Hotel(
                name='Mường Thanh Luxury Sài Gòn',
                location_id=locations[1].id,
                address='235 Nguyễn Văn Cừ, Q.1, TP.HCM',
                phone='028-3838-5555',
                email='saigon@muongthanh.com',
                description='Khách sạn cao cấp trung tâm',
                rating=4.7
            )
            db.session.add_all([hotel1, hotel2])
            db.session.commit()
            
            # Tạo rooms
            rooms = [
                Room(hotel_id=hotel1.id, room_number='101', room_type='Standard', 
                     price=800000, max_people=2, size=25, status='available'),
                Room(hotel_id=hotel1.id, room_number='201', room_type='Deluxe', 
                     price=1200000, max_people=3, size=35, status='available'),
                Room(hotel_id=hotel1.id, room_number='301', room_type='Suite', 
                     price=2000000, max_people=4, size=50, status='available'),
                Room(hotel_id=hotel2.id, room_number='102', room_type='Standard', 
                     price=900000, max_people=2, size=28, status='available'),
                Room(hotel_id=hotel2.id, room_number='202', room_type='Deluxe', 
                     price=1500000, max_people=3, size=40, status='available'),
            ]
            db.session.add_all(rooms)
            
            # Tạo promotions
            promo = Promotion(
                code='WELCOME2025',
                description='Giảm 10% cho khách hàng mới',
                discount_percent=10,
                min_amount=500000,
                max_uses=100,
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 12, 31),
                active=True
            )
            db.session.add(promo)
            
            db.session.commit()
            print("✅ Khởi tạo dữ liệu mẫu thành công!")
            print("📧 Admin: admin@muongthanh.com / admin123")
            print("📧 Customer: customer@example.com / 123456")


MOMO_CONFIG = {
    'partner_code': 'MOMOBKUN20180529',
    'access_key': 'klm05TvNBzhg7h7j',
    'secret_key': 'at67qH6mk8w5Y1nAyMoYKMWACiEi2bsa',
    'endpoint': 'https://test-payment.momo.vn/v2/gateway/api/create'
}
VNPAY_CONFIG = {
    'tmn_code': 'DEMOV210',
    'hash_secret': 'RAOEXHYVSDDIIENYWSLDIIZTANXUXZFJ',
    'payment_url': 'https://sandbox.vnpayment.vn/paymentv2/vpcpay.html'
}
ZALOPAY_CONFIG = {
    'app_id': '2553',
    'key1': 'PcY4iZIKFCIdgZvA6ueMcMHHUbRLYjPL',
    'key2': 'kLtgPl8HHhfvMuDHPwKfgfsY4Ydm9eIz',
    'endpoint': 'https://sb-openapi.zalopay.vn/v2/create'
}

# Khởi tạo payment services
momo_service = MoMoPayment(**MOMO_CONFIG)
vnpay_service = VNPayPayment(**VNPAY_CONFIG)
zalopay_service = ZaloPayPayment(**ZALOPAY_CONFIG)



# --- Decorators ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            from flask import request
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'admin':
            flash('Bạn không có quyền truy cập trang này', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def partner_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') not in ['partner', 'admin']:
            flash('Bạn không có quyền truy cập trang này', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# --- Routes  ---

@app.route('/')
def index():
    locations = Location.query.all()
    try:
        featured_rooms = Room.query.filter_by(status='available').order_by(Room.id).limit(6).all()
    except Exception as e:
        print(f"Error fetching featured rooms: {e}")
        featured_rooms = []

    return render_template('index.html', locations=locations, featured_rooms=featured_rooms)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        full_name = request.form.get('full_name')
        phone = request.form.get('phone')
        
        if not all([email, password, confirm_password, full_name, phone]):
            flash('Vui lòng điền đầy đủ thông tin', 'danger')
            return redirect(url_for('register'))
            
        if password != confirm_password:
            flash('Mật khẩu xác nhận không khớp', 'danger')
            return redirect(url_for('register'))
            
        if User.query.filter_by(email=email).first():
            flash('Email đã được sử dụng', 'danger')
            return redirect(url_for('register'))
        
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        
        new_user = User(
            email=email,
            password=hashed_password,
            full_name=full_name,
            phone=phone,
            role='customer'
        )
        
        try:
            db.session.add(new_user)
            db.session.commit()
            flash('Đăng ký thành công! Vui lòng đăng nhập', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            print(f"Error during registration: {str(e)}")
            flash('Có lỗi xảy ra, vui lòng thử lại', 'danger')
            return redirect(url_for('register'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        next_url = request.form.get('next') or request.args.get('next')
        
        user = User.query.filter_by(email=email).first()
        if user:
            is_valid = check_password_hash(user.password, password)
            
            if is_valid:
                session['user_id'] = user.id
                session['role'] = user.role
                session['full_name'] = user.full_name
                flash('Đăng nhập thành công!', 'success')
                if next_url:
                    return redirect(next_url)
                if user.role == 'admin':
                    return redirect(url_for('admin_dashboard'))
                elif user.role == 'partner':
                    return redirect(url_for('partner_dashboard'))
                else:
                    return redirect(url_for('index'))
            else:
                flash('Email hoặc mật khẩu không đúng', 'danger')
        else:
            flash('Email hoặc mật khẩu không đúng', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Đăng xuất thành công', 'success')
    return redirect(url_for('index'))


@app.route('/search')
def search():
    """
    Tìm kiếm phòng trống có tính đến khoảng thời gian check_in / check_out.
    """
    # Lấy tham số và chuyển đổi kiểu
    location_id = request.args.get('location', type=int)
    check_in_str = request.args.get('check_in')
    check_out_str = request.args.get('check_out')
    guests = request.args.get('guests', type=int)
    room_type = request.args.get('room_type')
    
    # --- 1. Xử lý và kiểm tra ngày tháng ---
    check_in = None
    check_out = None
    
    try:
        if check_in_str:
            check_in = datetime.strptime(check_in_str, '%Y-%m-%d').date()
        if check_out_str:
            check_out = datetime.strptime(check_out_str, '%Y-%m-%d').date()
            
        if check_in and check_out:
            if check_out <= check_in:
                flash('Ngày trả phòng phải sau ngày nhận phòng.', 'warning')
                check_in, check_out = None, None 

        elif (check_in_str or check_out_str) and not (check_in and check_out):
            flash('Vui lòng chọn đầy đủ ngày nhận và trả phòng hợp lệ.', 'warning')
            
    except ValueError:
        flash('Định dạng ngày tháng không hợp lệ.', 'danger')
        check_in, check_out = None, None 

    
    # --- 2. Áp dụng các bộ lọc cơ bản ---
    query = Room.query.filter_by(status='available').join(Hotel)
    
    if location_id:
        query = query.filter(Hotel.location_id == location_id)

    if guests:
        query = query.filter(Room.max_people >= guests)
        
    if room_type:
        query = query.filter(Room.room_type.ilike(f'%{room_type}%'))
        
    
    # --- 3. Áp dụng LỌC PHÒNG TRỐNG THEO NGÀY ---
    if check_in and check_out:
        booked_room_ids = db.session.query(Booking.room_id).filter(
            Booking.status.in_(['confirmed', 'pending']),
            Booking.check_out > check_in, 
            Booking.check_in < check_out 
        ).subquery()
        
        query = query.filter(not_(Room.id.in_(booked_room_ids)))

    
    rooms = query.all()
    locations = Location.query.all()
    
    rooms_data = []
    for room in rooms:
        rooms_data.append(SimpleNamespace(
            id=room.id,
            room_type=room.room_type,
            price=room.price,
            max_people=room.max_people,
            hotel_name=room.hotel.name
        ))

    # Truyền tham số tìm kiếm để form giữ trạng thái
    return render_template('search.html', 
                           rooms=rooms_data, 
                           locations=locations,
                           search_params={
                               'location': location_id,
                               'check_in': check_in_str,
                               'check_out': check_out_str,
                               'guests': guests,
                               'room_type': room_type
                           })

@app.route('/room/<int:room_id>')
def room_detail(room_id):
    """
    Hiển thị chi tiết phòng và thông tin đánh giá.
    Endpoint này phải tồn tại để url_for('room_detail', ...) hoạt động.
    """
    room = Room.query.get_or_404(room_id)
    
    reviews = Review.query.filter_by(room_id=room_id).order_by(Review.created_at.desc()).all()

    # Tính điểm trung bình 
    avg_rating = db.session.query(db.func.avg(Review.rating)).filter_by(room_id=room_id).scalar()
    
    # Kiểm tra quyền đánh giá
    can_review = False
    booking_to_review_id = None
    if 'user_id' in session:
        user_id = session['user_id']
        
        completed_bookings = Booking.query.filter(
            (Booking.user_id == user_id) & 
            (Booking.room_id == room_id) &
            (Booking.status == 'completed')
        ).all()

        for booking in completed_bookings:
            if not booking.review: 
                 can_review = True
                 booking_to_review_id = booking.id
                 break 
                 
    return render_template('room_detail.html', 
                             room=room, 
                             reviews=reviews, 
                             avg_rating=avg_rating,
                             can_review=can_review,
                             booking_to_review_id=booking_to_review_id)

@app.route('/booking/<int:room_id>', methods=['GET', 'POST'])
def booking(room_id):
    user = None
    room = None
    is_orm_room = False
    
    # Lấy thông tin phòng (có fallback)
    try:
        room = Room.query.get_or_404(room_id)
        is_orm_room = True
    except Exception as e:
        print(f"Error fetching Room ORM for booking id={room_id}: {e}")
        try:
            stmt = text('SELECT * FROM rooms WHERE id = :id')
            row = db.session.execute(stmt, {'id': room_id}).fetchone()
            if row is None:
                return ("Room not found", 404)
            data = dict(row._mapping) if hasattr(row, '_mapping') else dict(row)
            room = SimpleNamespace(**data)
            is_orm_room = False
        except Exception as e2:
            print(f"Fallback raw SQL also failed for booking id={room_id}: {e2}")
            return ("Room not found", 404)

    # Lấy thông tin user (nếu có)
    if 'user_id' in session:
        try:
            user = User.query.get(session['user_id'])
        except Exception:
            user = None
    
    if request.method == 'POST':
        try:
            # Lấy và validate dữ liệu đầu vào
            guest_name = request.form.get('guest_name')
            guest_phone = request.form.get('guest_phone')
            guest_address = request.form.get('guest_address')
            check_in_str = request.form.get('check_in')
            check_out_str = request.form.get('check_out')
            
            check_in = datetime.strptime(check_in_str, '%Y-%m-%d')
            check_out = datetime.strptime(check_out_str, '%Y-%m-%d')
            adults = int(request.form.get('adults', 1))
            children = int(request.form.get('children', 0))
            total_guests = adults + children
            
            # Validation
            now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            if check_in < now:
                flash('Ngày check-in phải từ ngày hiện tại trở đi', 'danger')
                return redirect(url_for('booking', room_id=room_id))
            if check_out <= check_in:
                flash('Ngày check-out phải sau ngày check-in', 'danger')
                return redirect(url_for('booking', room_id=room_id))
            if total_guests > getattr(room, 'max_people', 0):
                flash(f'Số lượng khách tối đa cho phòng này là {getattr(room, "max_people", 0)}', 'danger')
                return redirect(url_for('booking', room_id=room_id))
            
            # Kiểm tra phòng có available không (logic đã được chuẩn hóa)
            available = True
            if is_orm_room and hasattr(room, 'is_available') and callable(getattr(room, 'is_available')):
                available = room.is_available(check_in, check_out)
            else:
                # Manual check for SimpleNamespace or failed ORM load
                overlapping = Booking.query.filter(
                    Booking.room_id == room_id,
                    Booking.status != 'cancelled',
                    or_(
                        and_(Booking.check_in <= check_in, Booking.check_out > check_in),
                        and_(Booking.check_in < check_out, Booking.check_out >= check_out),
                        and_(Booking.check_in >= check_in, Booking.check_out <= check_out),
                    )
                ).all()
                available = len(overlapping) == 0

            if not available:
                flash('Phòng đã được đặt trong thời gian này', 'danger')
                return redirect(url_for('booking', room_id=room_id))
            
            # Tính tổng tiền
            nights = (check_out - check_in).days
            price_val = getattr(room, 'price', 0) or 0
            total_price = price_val * nights
            
            # Xử lý mã giảm giá (giữ nguyên logic)
            promotion_code = request.form.get('promotion_code')
            applied_promotion = None
            if promotion_code:
                promo = Promotion.query.filter_by(code=promotion_code, active=True).first()
                if promo and promo.start_date <= datetime.now() <= promo.end_date:
                    if total_price >= promo.min_amount:
                        if promo.max_uses is None or promo.current_uses < promo.max_uses:
                            discount = total_price * (promo.discount_percent / 100)
                            total_price -= discount
                            applied_promotion = promo
                        else:
                            flash('Mã giảm giá đã hết lượt sử dụng', 'warning')
                    else:
                        flash(f'Đơn hàng tối thiểu {promo.min_amount:,.0f}đ để sử dụng mã giảm giá này', 'warning')
            
            # Tạo booking mới
            new_booking = Booking(
                user_id=session.get('user_id'),
                room_id=room_id,
                check_in=check_in,
                check_out=check_out,
                adults=adults,
                children=children,
                total_price=total_price,
                promotion_code=promotion_code if applied_promotion else None,
                special_requests=request.form.get('special_requests'),
                guest_name=guest_name,
                guest_phone=guest_phone,
                guest_address=guest_address,
                status='pending',
                payment_status='unpaid'
            )
            
            db.session.add(new_booking)
            
            # Cập nhật số lần sử dụng mã giảm giá
            if applied_promotion:
                applied_promotion.current_uses += 1
            
            db.session.commit()
            flash('Đặt phòng thành công! Vui lòng thanh toán', 'success')
            return redirect(url_for('payment', booking_id=new_booking.id))
            
        except Exception as e:
            db.session.rollback()
            print(f"Error during booking: {str(e)}")
            flash('Có lỗi xảy ra trong quá trình đặt phòng', 'danger')
            return redirect(url_for('booking', room_id=room_id))
    
    # GET request - hiển thị form đặt phòng
    hotel = None
    try:
        hotel_id = getattr(room, 'hotel_id', None)
        if hotel_id is not None:
            hotel = Hotel.query.get(hotel_id)
    except Exception:
        hotel = None
    
    min_date = datetime.now().strftime('%Y-%m-%d')
    max_date = (datetime.now() + timedelta(days=365)).strftime('%Y-%m-%d')
    
    return render_template('booking.html', 
                         room=room,
                         hotel=hotel,
                         min_date=min_date,
                         max_date=max_date,
                         user=user)

@app.route('/payment/<int:booking_id>')
def payment(booking_id):
    """Trang chọn phương thức thanh toán"""
    booking = Booking.query.get_or_404(booking_id)
    
    if booking.user_id is not None:
        if 'user_id' not in session or booking.user_id != session.get('user_id'):
            flash('Không có quyền truy cập', 'error')
            return redirect(url_for('index'))
    
    # Tạo QR code cho thanh toán Banking (MUONGTHANH[BookingID])
    payload = f"MUONGTHANH{booking.id}"
    qr_b64 = generate_qr_base64(payload)
    
    return render_template('payment.html', booking=booking, qr_b64=qr_b64)


# --- Dùng cho mục đích mô phỏng thành công nhanh (MOMO) ---
@app.route('/payment/momo/simulate/<int:booking_id>')
def payment_momo_simulate(booking_id):
    """Mô phỏng Thanh toán qua MoMo thành công"""
    try:
        booking = Booking.query.get_or_404(booking_id)
        
        # Kiểm tra quyền truy cập (giữ nguyên)
        if booking.user_id is not None:
            if 'user_id' not in session or booking.user_id != session.get('user_id'):
                flash('Không có quyền truy cập', 'error')
                return redirect(url_for('index'))
        
        # Cập nhật trạng thái thành công
        booking.payment_method = 'momo'
        booking.payment_status = 'paid'
        booking.status = 'confirmed'
        db.session.commit()
        
        flash('Mô phỏng: Thanh toán MoMo thành công!', 'success')
        return redirect(url_for('booking_confirm', booking_id=booking.id))
            
    except Exception as e:
        print(f"MoMo Payment Simulate Error: {str(e)}")
        flash(f'Lỗi khi mô phỏng thanh toán MoMo: {str(e)}', 'error')
        return redirect(url_for('payment', booking_id=booking_id))


# --- Dùng cho mục đích mô phỏng thành công nhanh (ZALOPAY) ---
@app.route('/payment/zalopay/simulate/<int:booking_id>') # Đổi tên route để tránh trùng lặp
def payment_zalopay_simulate(booking_id):
    """Mô phỏng Thanh toán qua ZaloPay thành công"""
    try:
        booking = Booking.query.get_or_404(booking_id)
        
        # Kiểm tra quyền truy cập (giữ nguyên)
        if booking.user_id is not None:
            if 'user_id' not in session or booking.user_id != session.get('user_id'):
                flash('Không có quyền truy cập', 'error')
                return redirect(url_for('index'))
        
        # Cập nhật trạng thái thành công
        booking.payment_method = 'zalopay'
        booking.payment_status = 'paid'
        booking.status = 'confirmed'
        db.session.commit()
        
        flash('Mô phỏng: Thanh toán ZaloPay thành công!', 'success')
        return redirect(url_for('booking_confirm', booking_id=booking.id))
            
    except Exception as e:
        print(f"ZaloPay Payment Simulate Error: {str(e)}")
        flash(f'Lỗi khi mô phỏng thanh toán ZaloPay: {str(e)}', 'error')
        return redirect(url_for('payment', booking_id=booking_id))


@app.route('/booking/confirm/<int:booking_id>')
def booking_confirm(booking_id):
    """Trang xác nhận đặt phòng sau khi thanh toán/ghi nhận thanh toán"""
    booking = Booking.query.get_or_404(booking_id)
    
    # Tạo QR code cho trang xác nhận
    payload = f"MUONGTHANH{booking.id};ROOM:{booking.room_id}"
    qr_b64 = generate_qr_base64(payload)
    
    # Lấy thông tin phòng để hiển thị chi tiết hơn trong trang xác nhận
    room = Room.query.get(booking.room_id)
    
    return render_template('booking_confirm.html', booking=booking, qr_b64=qr_b64, room=room)


# --- Khác (Giữ nguyên) ---
@app.route('/book-now/<int:room_id>')
@login_required
def book_now(room_id):
    # ... (giữ nguyên)
    room_obj = None
    try:
        room_obj = Room.query.get_or_404(room_id)
    except Exception as e:
        print(f"Error fetching Room ORM for quick-book id={room_id}: {e}")
        try:
            stmt = text('SELECT * FROM rooms WHERE id = :id')
            row = db.session.execute(stmt, {'id': room_id}).fetchone()
            if row is None:
                flash('Phòng không tồn tại', 'danger')
                return redirect(url_for('index'))
            data = dict(row._mapping) if hasattr(row, '_mapping') else dict(row)
            room_obj = SimpleNamespace(**data)
        except Exception as e2:
            print(f"Fallback raw SQL failed for quick-book id={room_id}: {e2}")
            flash('Không thể lấy thông tin phòng, thử lại sau', 'danger')
            return redirect(url_for('index'))

    check_in = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0) + timedelta(days=1)
    check_out = check_in + timedelta(days=1)

    try:
        available = True
        # Nếu là ORM Room instance, sử dụng helper
        if hasattr(room_obj, 'is_available') and callable(getattr(room_obj, 'is_available')):
            available = room_obj.is_available(check_in, check_out)
        else:
            # Manual availability check for SimpleNamespace or failed ORM load
            overlapping = Booking.query.filter(
                Booking.room_id == room_id,
                Booking.status != 'cancelled',
                or_(
                    and_(Booking.check_in <= check_in, Booking.check_out > check_in),
                    and_(Booking.check_in < check_out, Booking.check_out >= check_out),
                    and_(Booking.check_in >= check_in, Booking.check_out <= check_out),
                )
            ).all()
            available = len(overlapping) == 0

        if not available:
            flash('Phòng không khả dụng cho ngày mặc định, vui lòng đặt thủ công', 'danger')
            return redirect(url_for('room_detail', room_id=room_id))

        price = getattr(room_obj, 'price', None)
        booking = Booking(
            user_id=session['user_id'],
            room_id=room_id,
            check_in=check_in,
            check_out=check_out,
            adults=1,
            children=0,
            total_price=price or 0,
            status='pending',
            payment_status='unpaid'
        )

        db.session.add(booking)
        db.session.commit()
        return redirect(url_for('payment', booking_id=booking.id))
    except Exception as e:
        db.session.rollback()
        print(f"Error creating quick booking: {e}")
        flash('Không thể tạo đặt phòng nhanh, vui lòng thử lại', 'danger')
        return redirect(url_for('room_detail', room_id=room_id))


from datetime import datetime
# ...
@app.route('/my-bookings')
@login_required
def my_bookings():
    user_id = session['user_id']
    bookings = Booking.query.filter_by(user_id=user_id)\
        .order_by(Booking.created_at.desc())\
        .all()

    # Thêm cờ đánh giá cho mỗi booking
    for booking in bookings:
        # Kiểm tra điều kiện đánh giá: Đã hoàn thành (completed) và chưa có review
        can_review = (
            booking.status == 'completed' and 
            not booking.review # Kiểm tra mối quan hệ ngược từ Review
        )
        # Gán thuộc tính tạm thời vào đối tượng booking
        setattr(booking, 'can_review', can_review)

    return render_template('my_bookings.html', bookings=bookings, now=datetime.now())

    # Thêm biến 'now'
    return render_template('my_bookings.html', bookings=bookings, now=datetime.now())



@app.route('/payment/vnpay/<int:booking_id>')
def payment_vnpay(booking_id):
    """
    Route mô phỏng thanh toán VNPay
    Trong production thực tế, route này sẽ tạo URL VNPay và redirect user đến cổng thanh toán
    Ở đây chúng ta chỉ mô phỏng kết quả thành công
    """
    try:
        booking = Booking.query.get_or_404(booking_id)
        
        # Kiểm tra quyền truy cập
        if booking.user_id is not None:
            if 'user_id' not in session or booking.user_id != session.get('user_id'):
                flash('Không có quyền truy cập', 'error')
                return redirect(url_for('index'))
        
        # Mô phỏng: Cập nhật trạng thái thanh toán thành công
        booking.payment_method = 'vnpay'
        booking.payment_status = 'paid'
        booking.status = 'confirmed'
        db.session.commit()
        
        flash('Mô phỏng: Thanh toán VNPay thành công!', 'success')
        return redirect(url_for('booking_confirm', booking_id=booking.id))
            
    except Exception as e:
        print(f"VNPay Payment Simulate Error: {str(e)}")
        flash(f'Lỗi khi mô phỏng thanh toán VNPay: {str(e)}', 'error')
        return redirect(url_for('payment', booking_id=booking_id))


@app.route('/payment/banking/confirm/<int:booking_id>', methods=['POST'])
def confirm_qr_payment(booking_id):
    """
    Route xác nhận thanh toán chuyển khoản ngân hàng
    User click "Tôi đã chuyển khoản" sau khi quét QR code
    """
    try:
        booking = Booking.query.get_or_404(booking_id)
        
        # Kiểm tra quyền truy cập
        if booking.user_id is not None:
            if 'user_id' not in session or booking.user_id != session.get('user_id'):
                flash('Không có quyền truy cập', 'error')
                return redirect(url_for('index'))
        
        # Cập nhật trạng thái - đánh dấu là đang chờ xác nhận từ admin
        booking.payment_method = 'banking'
        booking.payment_status = 'pending'  # Chờ admin xác nhận
        booking.status = 'pending'
        db.session.commit()
        
        flash('Đã ghi nhận thanh toán của bạn! Đơn đặt phòng đang chờ xác nhận từ quản trị viên.', 'info')
        return redirect(url_for('booking_confirm', booking_id=booking.id))
            
    except Exception as e:
        db.session.rollback()
        print(f"Banking Payment Confirm Error: {str(e)}")
        flash(f'Lỗi khi xác nhận thanh toán: {str(e)}', 'error')
        return redirect(url_for('payment', booking_id=booking_id))


# ===== ROUTE ADMIN XÁC NHẬN THANH TOÁN CHUYỂN KHOẢN =====
@app.route('/admin/bookings/<int:booking_id>/confirm-payment', methods=['POST'])
@admin_required
def admin_confirm_payment(booking_id):
    """
    Route cho admin xác nhận thanh toán chuyển khoản ngân hàng
    """
    try:
        booking = Booking.query.get_or_404(booking_id)
        
        # Chỉ confirm những booking có payment_status = 'pending'
        if booking.payment_status == 'pending':
            booking.payment_status = 'paid'
            booking.status = 'confirmed'
            db.session.commit()
            flash(f'Đã xác nhận thanh toán cho đặt phòng #{booking.id}', 'success')
        else:
            flash('Đặt phòng này không ở trạng thái chờ xác nhận', 'warning')
            
        return redirect(url_for('admin_bookings'))
    except Exception as e:
        db.session.rollback()
        print(f"Admin Confirm Payment Error: {str(e)}")
        flash(f'Lỗi khi xác nhận thanh toán: {str(e)}', 'error')
        return redirect(url_for('admin_bookings'))


@app.route('/admin/bookings/<int:booking_id>/reject-payment', methods=['POST'])
@admin_required
def admin_reject_payment(booking_id):
    """
    Route cho admin từ chối thanh toán (nếu chuyển khoản không hợp lệ)
    """
    try:
        booking = Booking.query.get_or_404(booking_id)
        
        if booking.payment_status == 'pending':
            booking.payment_status = 'failed'
            booking.status = 'cancelled'
            db.session.commit()
            flash(f'Đã từ chối thanh toán cho đặt phòng #{booking.id}', 'success')
        else:
            flash('Đặt phòng này không ở trạng thái chờ xác nhận', 'warning')
            
        return redirect(url_for('admin_bookings'))
    except Exception as e:
        db.session.rollback()
        print(f"Admin Reject Payment Error: {str(e)}")
        flash(f'Lỗi khi từ chối thanh toán: {str(e)}', 'error')
        return redirect(url_for('admin_bookings'))

@app.route('/admin/room-map')
@admin_required
def admin_room_map():
    """Sơ đồ phòng theo khách sạn"""
    check_date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    check_date = datetime.strptime(check_date_str, '%Y-%m-%d')
    
    locations = Location.query.all()
    hotels = Hotel.query.all()
    
    # Gán trạng thái cho mỗi phòng
    for hotel in hotels:
        for room in hotel.rooms:
            # Tìm booking hiện tại
            current_booking = Booking.query.filter(
                Booking.room_id == room.id,
                Booking.check_in <= check_date,
                Booking.check_out > check_date,
                Booking.status.in_(['confirmed', 'pending'])
            ).first()
            
            room.current_booking = current_booking
            
            if room.status == 'maintenance':
                room.current_status = 'maintenance'
            elif current_booking:
    # Nếu đã check-in thực tế
             if current_booking.status == 'checked_in':
              room.current_status = 'occupied'

    # Chưa check-in nhưng đã đặt — DÙ hôm nay = ngày check-in vẫn là "reserved"
             elif current_booking.status in ['confirmed', 'pending']:
              room.current_status = 'reserved'

            else:
             room.current_status = 'available'

            
            # ✅ SỬA: Đảm bảo floor luôn có giá trị
            if room.floor is None or room.floor == 0:
                try:
                    # Lấy ký tự đầu tiên của room_number làm tầng
                    room.floor = int(room.room_number[0]) if room.room_number and len(room.room_number) > 0 else 1
                except (ValueError, IndexError):
                    room.floor = 1
    
    return render_template('admin/room_map.html',
                         locations=locations,
                         hotels=hotels,
                         today=check_date.strftime('%Y-%m-%d'))


@app.route('/admin/room/<int:room_id>/detail')
@admin_required
def admin_room_detail(room_id):
    """API trả về chi tiết phòng"""
    room = Room.query.get_or_404(room_id)
    
    # Booking hiện tại
    now = datetime.now()
    current_booking = Booking.query.filter(
        Booking.room_id == room_id,
        Booking.check_in <= now,
        Booking.check_out > now,
        Booking.status != 'cancelled'
    ).first()
    
    # Upcoming bookings
    upcoming_bookings = Booking.query.filter(
        Booking.room_id == room_id,
        Booking.check_in > now,
        Booking.status != 'cancelled'
    ).order_by(Booking.check_in).limit(5).all()
    
    # Xác định trạng thái
    if room.status == 'maintenance':
        current_status = 'maintenance'
    elif current_booking:
        if current_booking.check_in.date() <= now.date():
            current_status = 'occupied'
        else:
            current_status = 'reserved'
    else:
        current_status = 'available'
    
    return jsonify({
        'id': room.id,
        'room_number': room.room_number,
        'room_type': room.room_type,
        'price': room.price,
        'max_people': room.max_people,
        'current_status': current_status,
        'current_booking': {
            'id': current_booking.id,
            'guest_name': current_booking.guest_name,
            'check_in': current_booking.check_in.isoformat(),
            'check_out': current_booking.check_out.isoformat(),
            'total_price': current_booking.total_price
        } if current_booking else None,
        'upcoming_bookings': [{
            'id': b.id,
            'guest_name': b.guest_name,
            'check_in': b.check_in.isoformat(),
            'check_out': b.check_out.isoformat()
        } for b in upcoming_bookings]
    })


# ===== QUẢN LÝ ĐÁNH GIÁ =====
@app.route('/admin/reviews')
@admin_required
def admin_reviews():
    """Quản lý đánh giá"""
    # Lấy reviews pending trước, sau đó các reviews khác
    pending_reviews = Review.query.filter_by(status='pending')\
        .order_by(Review.created_at.desc()).all()
    
    other_reviews = Review.query.filter(Review.status != 'pending')\
        .order_by(Review.created_at.desc()).all()
    
    # Ghép lại: pending trước, other sau
    reviews = pending_reviews + other_reviews
    
    stats = {
        'total': Review.query.count(),
        'pending': Review.query.filter_by(status='pending').count(),
        'approved': Review.query.filter_by(status='approved').count(),
        'average_rating': db.session.query(func.avg(Review.rating)).scalar() or 0
    }
    
    return render_template('admin/reviews.html', reviews=reviews, stats=stats)


@app.route('/admin/reviews/<int:review_id>/approve', methods=['POST'])
@admin_required
def admin_approve_review(review_id):
    """Duyệt đánh giá"""
    review = Review.query.get_or_404(review_id)
    review.status = 'approved'
    
    try:
        db.session.commit()
        flash('Đã duyệt đánh giá', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi: {str(e)}', 'error')
    
    return redirect(url_for('admin_reviews'))


@app.route('/admin/reviews/<int:review_id>/reject', methods=['POST'])
@admin_required
def admin_reject_review(review_id):
    """Từ chối đánh giá"""
    review = Review.query.get_or_404(review_id)
    review.status = 'rejected'
    
    try:
        db.session.commit()
        flash('Đã từ chối đánh giá', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi: {str(e)}', 'error')
    
    return redirect(url_for('admin_reviews'))


@app.route('/admin/reviews/<int:review_id>/reply', methods=['POST'])
@admin_required
def admin_reply_review(review_id):
    """Trả lời đánh giá"""
    review = Review.query.get_or_404(review_id)
    reply = request.form.get('reply', '').strip()
    
    if not reply:
        flash('Vui lòng nhập nội dung phản hồi', 'danger')
        return redirect(url_for('admin_reviews'))
    
    review.admin_reply = reply
    review.reply_at = datetime.now()
    
    try:
        db.session.commit()
        flash('Đã gửi phản hồi', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi: {str(e)}', 'error')
    
    return redirect(url_for('admin_reviews'))


# ===== THỐNG KÊ DOANH THU =====
@app.route('/admin/revenue')
@admin_required
def admin_revenue():
    """Thống kê doanh thu"""
    period = request.args.get('period', 'month')
    
    # Tính toán khoảng thời gian
    now = datetime.now()
    if period == 'day':
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = now
    elif period == 'week':
        start_date = now - timedelta(days=now.weekday())
        end_date = now
    elif period == 'month':
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now
    elif period == 'year':
        start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now
    else:
        start_date = request.args.get('start_date', now - timedelta(days=30))
        end_date = request.args.get('end_date', now)
    
    # Tổng doanh thu
    total_revenue = db.session.query(func.sum(Booking.total_price))\
        .filter(
            Booking.payment_status == 'paid',
            Booking.created_at >= start_date,
            Booking.created_at <= end_date
        ).scalar() or 0
    
    # Tổng đặt phòng
    total_bookings = Booking.query.filter(
        Booking.created_at >= start_date,
        Booking.created_at <= end_date
    ).count()
    
    # Giá trị TB
    avg_booking_value = total_revenue / total_bookings if total_bookings > 0 else 0
    
    # Tỷ lệ lấp đầy
    total_rooms = Room.query.filter_by(status='available').count()
    occupied_rooms = Booking.query.filter(
        Booking.status == 'confirmed',
        Booking.check_in <= now,
        Booking.check_out >= now
    ).count()
    occupancy_rate = (occupied_rooms / total_rooms * 100) if total_rooms > 0 else 0
    
    # So sánh với tháng trước
    prev_start = start_date - timedelta(days=30)
    prev_revenue = db.session.query(func.sum(Booking.total_price))\
        .filter(
            Booking.payment_status == 'paid',
            Booking.created_at >= prev_start,
            Booking.created_at < start_date
        ).scalar() or 1
    
    revenue_growth = ((total_revenue - prev_revenue) / prev_revenue * 100) if prev_revenue > 0 else 0
    
    prev_bookings = Booking.query.filter(
        Booking.created_at >= prev_start,
        Booking.created_at < start_date
    ).count() or 1
    
    booking_growth = ((total_bookings - prev_bookings) / prev_bookings * 100) if prev_bookings > 0 else 0
    
    stats = {
        'total_revenue': total_revenue,
        'total_bookings': total_bookings,
        'avg_booking_value': avg_booking_value,
        'occupancy_rate': round(occupancy_rate, 1),
        'revenue_growth': round(revenue_growth, 1),
        'booking_growth': round(booking_growth, 1),
        'avg_value_change': round((avg_booking_value - (prev_revenue/prev_bookings if prev_bookings > 0 else 0)) / (prev_revenue/prev_bookings if prev_bookings > 0 else 1) * 100, 1),
        'occupancy_change': 5.2
    }
    
    # ✅ SỬA: Dữ liệu biểu đồ doanh thu (30 ngày gần nhất) - SQL Server compatible
    revenue_data = []
    revenue_labels = []
    
    for i in range(30, 0, -1):
        day = now - timedelta(days=i)
        # Tạo khoảng thời gian cho 1 ngày
        start_of_day = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)
        
        day_revenue = db.session.query(func.sum(Booking.total_price))\
            .filter(
                Booking.payment_status == 'paid',
                Booking.created_at >= start_of_day,
                Booking.created_at < end_of_day
            ).scalar() or 0
        
        revenue_data.append(float(day_revenue))
        revenue_labels.append(day.strftime('%d/%m'))
    
    # Trạng thái booking
    booking_status_data = [
        Booking.query.filter_by(status='pending').count(),
        Booking.query.filter_by(status='confirmed').count(),
        Booking.query.filter_by(status='completed').count(),
        Booking.query.filter_by(status='cancelled').count()
    ]
    
    # Phương thức thanh toán
    payment_method_data = [
        Booking.query.filter_by(payment_method='momo', payment_status='paid').count(),
        Booking.query.filter_by(payment_method='vnpay', payment_status='paid').count(),
        Booking.query.filter_by(payment_method='zalopay', payment_status='paid').count(),
        Booking.query.filter_by(payment_method='banking', payment_status='paid').count()
    ]
    
    # Top 5 phòng doanh thu cao
    try:
        top_rooms_raw = db.session.query(
            Room,
            func.sum(Booking.total_price).label('revenue'),
            func.count(Booking.id).label('bookings_count')
        ).join(Booking)\
        .filter(Booking.payment_status == 'paid')\
        .group_by(Room.id)\
        .order_by(func.sum(Booking.total_price).desc())\
        .limit(5).all()
        
        top_rooms = []
        for room, revenue, count in top_rooms_raw:
            room.revenue = revenue
            room.bookings_count = count
            top_rooms.append(room)
    except Exception as e:
        print(f"Error fetching top rooms: {e}")
        top_rooms = []
    
    # Booking gần đây
    recent_bookings = Booking.query.filter(
        Booking.payment_status == 'paid'
    ).order_by(Booking.created_at.desc()).limit(20).all()
    
    return render_template('admin/revenue.html',
                         stats=stats,
                         revenue_data=revenue_data,
                         revenue_labels=revenue_labels,
                         booking_status_data=booking_status_data,
                         payment_method_data=payment_method_data,
                         top_rooms=top_rooms,
                         recent_bookings=recent_bookings)


@app.route('/admin/revenue/export')
@admin_required
def admin_revenue_export():
    """Xuất báo cáo doanh thu"""
    # TODO: Implement CSV/Excel export
    flash('Tính năng xuất báo cáo đang được phát triển', 'info')
    return redirect(url_for('admin_revenue'))


# ===== CẬP NHẬT DASHBOARD ADMIN =====
@app.route('/admin/dashboard')
@admin_required # Giả định hàm admin_required đã được định nghĩa
def admin_dashboard():
    # 1. Thống kê cơ bản
    total_users = User.query.count()
    total_bookings = Booking.query.count()
    total_rooms = Room.query.count()

    # 2. Đếm reviews đang chờ duyệt (Sử dụng cột status mới)
    pending_reviews = Review.query.filter_by(status='pending').count() 

    # 3. Tính tổng doanh thu từ các booking đã hoàn thành ('completed')
    revenue = db.session.query(func.sum(Booking.total_price)).filter(
        Booking.status == 'completed',
        Booking.payment_status == 'paid' # Chỉ tính những đơn đã thanh toán
    ).scalar()
    total_revenue = revenue if revenue is not None else 0

    # 4. Thống kê theo tháng cho biểu đồ
    # Tính toán doanh thu theo tháng trong 6 tháng gần nhất
    six_months_ago = datetime.now() - timedelta(days=180)
    
    # Query để lấy tổng giá và tháng/năm của các booking đã hoàn thành
    year_expr = extract('year', Booking.check_in)
    month_expr = extract('month', Booking.check_in)
    monthly_data = db.session.query(
    year_expr.label('year'),
    month_expr.label('month'),
    func.sum(Booking.total_price).label('revenue')
).filter(
        Booking.status == 'completed',
        Booking.payment_status == 'paid',
        Booking.check_in >= six_months_ago.date()
    ).group_by(
        year_expr, month_expr 
    ).order_by(
        year_expr, month_expr
    ).all()

    # Chuyển đổi kết quả truy vấn thành format phù hợp cho biểu đồ (tên tháng, doanh thu)
    months = ["Tháng 1", "Tháng 2", "Tháng 3", "Tháng 4", "Tháng 5", "Tháng 6", 
              "Tháng 7", "Tháng 8", "Tháng 9", "Tháng 10", "Tháng 11", "Tháng 12"]
    
    # Khởi tạo dữ liệu cho 6 tháng gần nhất (để tránh lỗ hổng dữ liệu)
    data_points = defaultdict(int)
    current_date = datetime.now().date()
    
    for i in range(6):
        target_month = (current_date.month - i - 1) % 12 + 1
        target_year = current_date.year if current_date.month >= target_month else current_date.year - 1
        key = (target_year, target_month)
        data_points[key] = 0

    # Cập nhật dữ liệu thực tế từ DB
    for year, month, revenue in monthly_data:
        data_points[(int(year), int(month))] = float(revenue)

    # Sắp xếp và format lại
    sorted_data = sorted(data_points.items(), key=lambda item: item[0])
    
    chart_labels = [f"{months[m[1]-1]}/{m[0]}" for m, r in sorted_data]
    chart_data = [r for m, r in sorted_data]


    context = {
        'total_users': total_users,
        'total_bookings': total_bookings,
        'total_rooms': total_rooms,
        'pending_reviews': pending_reviews,
        'total_revenue': total_revenue,
        'chart_labels': json.dumps(chart_labels),
        'chart_data': json.dumps(chart_data)
    }
    
    return render_template('admin/dashboard.html', **context)

# ===== ROUTE ADMIN QUẢN LÝ BOOKINGS =====
@app.route('/admin/bookings')
@admin_required
def admin_bookings():
    """Trang quản lý tất cả các đặt phòng"""
    # Lấy bookings pending trước
    pending_bookings = Booking.query.filter_by(payment_status='pending')\
        .order_by(Booking.created_at.desc()).all()
    
    # Lấy các bookings khác
    other_bookings = Booking.query.filter(Booking.payment_status != 'pending')\
        .order_by(Booking.created_at.desc()).all()
    
    # Ghép lại
    all_bookings = pending_bookings + other_bookings
    
    return render_template('admin/bookings.html', bookings=all_bookings)



# ===== ROUTE: THÊM ĐÁNH GIÁ =====
@app.route('/review/<int:booking_id>/add', methods=['POST'])
@login_required
def add_review(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    user = User.query.get(session['user_id'])
    
    # 1. Kiểm tra điều kiện:
    if booking.user_id != user.id or booking.status != 'completed':
        flash('Bạn không thể đánh giá đơn đặt phòng này.', 'danger')
        return redirect(url_for('my_bookings'))

    # Sử dụng thuộc tính 'review' từ backref trong Review Model
    if booking.review: 
        flash('Đơn đặt phòng này đã được đánh giá.', 'warning')
        return redirect(url_for('room_detail', room_id=booking.room_id))

    # 2. Lấy dữ liệu từ form
    try:
        rating = int(request.form.get('rating'))
        comment = request.form.get('comment')
        
        if not (1 <= rating <= 5):
            flash('Điểm đánh giá phải từ 1 đến 5.', 'danger')
            return redirect(url_for('room_detail', room_id=booking.room_id))

        # 3. Tạo và lưu đánh giá mới
        new_review = Review(
            room_id=booking.room_id,
            user_id=user.id,
            booking_id=booking_id,
            rating=rating,
            comment=comment
        )
        db.session.add(new_review)
        
        # Cập nhật điểm trung bình (Tùy chọn)
        # avg_rating = db.session.query(db.func.avg(Review.rating)).filter_by(room_id=booking.room_id).scalar()
        # if booking.room:
        #     booking.room.rating = avg_rating 

        db.session.commit()
        
        flash('Cảm ơn bạn đã gửi đánh giá!', 'success')
    
    except Exception as e:
        db.session.rollback()
        print(f"Error adding review: {e}")
        flash('Đã xảy ra lỗi trong quá trình gửi đánh giá.', 'danger')

    return redirect(url_for('my_bookings'))



# ===== ROUTE: TÀI KHOẢN CỦA TÔI VÀ THỐNG KÊ =====
@app.route('/my_account', methods=['GET', 'POST'])
@login_required
def my_account():
    """Hiển thị thông tin cá nhân và các chỉ số thống kê đặt phòng của người dùng."""
    user = User.query.get(session['user_id'])
    
    # 1. TÍNH TOÁN CÁC CHỈ SỐ THỐNG KÊ (STATS)
    
    # Tổng số lần đặt phòng
    total_bookings = Booking.query.filter_by(user_id=user.id).count()
    
    # Số lần đặt phòng đã hoàn thành (Sử dụng toán tử & thay cho and_ để tránh lỗi)
    completed_bookings = Booking.query.filter(
        (Booking.user_id == user.id) & (Booking.status == 'completed')
    ).count()
    
    # Số lần hủy
    cancelled_bookings = Booking.query.filter(
        (Booking.user_id == user.id) & (Booking.status == 'cancelled')
    ).count()

    # Tổng số tiền đã chi (chỉ tính các booking đã hoàn thành)
    total_spent_result = db.session.query(
        db.func.sum(Booking.total_price)
    ).filter(
        (Booking.user_id == user.id) & (Booking.status == 'completed')
    ).scalar()

    total_spent = total_spent_result if total_spent_result else 0
    
    # Tạo dictionary stats để truyền sang template
    stats = {
        'total_bookings': total_bookings,
        'completed_bookings': completed_bookings,
        'cancelled_bookings': cancelled_bookings,
        'total_spent': total_spent 
    }

    # 2. XỬ LÝ POST (Hiện tại không cần thiết vì bạn đã có route update_account riêng)
    # Phần POST/cập nhật thông tin đã được tách ra update_account và change_password

    # 3. TRUYỀN BIẾN STATS VÀO TEMPLATE
    # Biến stats đã được truyền vào my_account.html, khắc phục lỗi UndefinedError
    return render_template('my_account.html', user=user, stats=stats)

# ===== ROUTE: CẬP NHẬT THÔNG TIN TÀI KHOẢN =====
@app.route('/my-account/update', methods=['POST'])
@login_required
def update_account():
    """Cập nhật thông tin tài khoản"""
    user = User.query.get(session['user_id'])
    
    full_name = request.form.get('full_name')
    phone = request.form.get('phone')
    address = request.form.get('address')
    
    if full_name:
        user.full_name = full_name
        session['full_name'] = full_name
    if phone:
        user.phone = phone
    if address:
        user.address = address
    
    try:
        db.session.commit()
        flash('Cập nhật thông tin thành công!', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"Error updating account: {str(e)}")
        flash('Có lỗi xảy ra, vui lòng thử lại', 'danger')
    
    return redirect(url_for('my_account'))


# ===== ROUTE: ĐỔI MẬT KHẨU =====
@app.route('/my-account/change-password', methods=['POST'])
@login_required
def change_password():
    """Đổi mật khẩu"""
    user = User.query.get(session['user_id'])
    
    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')
    
    # Kiểm tra mật khẩu hiện tại
    if not check_password_hash(user.password, current_password):
        flash('Mật khẩu hiện tại không đúng', 'danger')
        return redirect(url_for('my_account'))
    
    # Kiểm tra mật khẩu mới
    if new_password != confirm_password:
        flash('Mật khẩu xác nhận không khớp', 'danger')
        return redirect(url_for('my_account'))
    
    if len(new_password) < 6:
        flash('Mật khẩu mới phải có ít nhất 6 ký tự', 'danger')
        return redirect(url_for('my_account'))
    
    # Cập nhật mật khẩu
    user.password = generate_password_hash(new_password, method='pbkdf2:sha256')
    
    try:
        db.session.commit()
        flash('Đổi mật khẩu thành công!', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"Error changing password: {str(e)}")
        flash('Có lỗi xảy ra, vui lòng thử lại', 'danger')
    
    return redirect(url_for('my_account'))


# ===== HELPER: TỰ ĐỘNG CẬP NHẬT TRẠNG THÁI BOOKING =====
@app.before_request
def auto_update_booking_status():
    """Tự động cập nhật trạng thái booking thành completed sau checkout"""
    # ... (giữ nguyên logic)
    if 'user_id' in session:
        now = datetime.now()
        
        # Tìm các booking đã qua check_out nhưng vẫn là confirmed
        expired_bookings = Booking.query.filter(
            Booking.user_id == session['user_id'],
            Booking.status == 'confirmed',
            Booking.check_out < now
        ).all()
        
        for booking in expired_bookings:
            booking.status = 'completed'
        
        if expired_bookings:
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()


# ===== ROUTE: HỦY ĐẶT PHÒNG (OPTIONAL) =====
@app.route('/booking/<int:booking_id>/cancel', methods=['POST'])
@login_required
def cancel_booking(booking_id):
    """Hủy đặt phòng (chỉ khi chưa check-in)"""
    booking = Booking.query.get_or_404(booking_id)
    
    # Kiểm tra quyền
    if booking.user_id != session['user_id']:
        flash('Bạn không có quyền hủy đặt phòng này', 'error')
        return redirect(url_for('my_bookings'))
    
    # Chỉ cho phép hủy nếu chưa check-in và chưa bị hủy
    now = datetime.now()
    if booking.check_in <= now:
        flash('Không thể hủy đặt phòng đã đến ngày check-in', 'warning')
        return redirect(url_for('booking_detail', booking_id=booking_id))
    
    if booking.status == 'cancelled':
        flash('Đặt phòng này đã bị hủy trước đó', 'info')
        return redirect(url_for('booking_detail', booking_id=booking_id))
    
    # Hủy booking
    booking.status = 'cancelled'
    
    # Nếu đã thanh toán, có thể thêm logic hoàn tiền ở đây
    if booking.payment_status == 'paid':
        # TODO: Xử lý hoàn tiền
        flash('Đặt phòng đã được hủy. Vui lòng liên hệ để được hoàn tiền.', 'info')
    
    try:
        db.session.commit()
        flash('Đã hủy đặt phòng thành công', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"Error cancelling booking: {str(e)}")
        flash('Có lỗi xảy ra, vui lòng thử lại', 'danger')
    
    return redirect(url_for('my_bookings'))


@app.route('/admin')
@admin_required
def admin():
    """Redirect /admin to /admin/dashboard"""
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/promotions/add', methods=['POST'])
@admin_required
def admin_add_promotion():
    code = request.form.get('code')
    discount_percent = request.form.get('discount_percent')
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')
    max_uses = request.form.get('max_uses')

    promo = Promotion(
        code=code,
        discount_percent=float(discount_percent),
        start_date=datetime.strptime(start_date, "%Y-%m-%d"),
        end_date=datetime.strptime(end_date, "%Y-%m-%d"),
        max_uses=int(max_uses),
        current_uses=0,
        active=True
    )

    db.session.add(promo)
    db.session.commit()

    return redirect(url_for('admin_promotions'))

@app.route('/admin/promotions')
@admin_required
def admin_promotions():
    promotions = Promotion.query.all()

    # Tính stats cho dashboard khuyến mãi
    stats = {
        "total": len(promotions),
        "active": sum(1 for p in promotions if p.active),
        "total_uses": sum(p.current_uses for p in promotions),
        "total_discount": sum(
            (p.discount_percent / 100) * 1_000_000  # giá trị tạm demo
            for p in promotions
        )
    }

    return render_template(
        'admin/promotions.html',
        promotions=promotions,
        stats=stats,
        now=datetime.now()
    )
@app.route('/admin/promotions/toggle/<int:promo_id>', methods=['POST'])
@admin_required
def admin_toggle_promotion(promo_id):
    promo = Promotion.query.get_or_404(promo_id)

    # Đảo trạng thái
    promo.active = not promo.active

    db.session.commit()

    return redirect(url_for('admin_promotions'))


@app.route('/admin/promotions/<int:promo_id>/delete', methods=['POST'])
@admin_required
def admin_delete_promotion(promo_id):
    """Xóa khuyến mãi"""
    try:
        promo = Promotion.query.get_or_404(promo_id)
        
        # Kiểm tra xem mã có đang được sử dụng không
        if promo.current_uses > 0:
            flash('Không thể xóa mã khuyến mãi đã được sử dụng. Hãy tắt thay vì xóa.', 'warning')
            return redirect(url_for('admin_promotions'))
        
        db.session.delete(promo)
        db.session.commit()
        
        flash(f'Đã xóa mã khuyến mãi {promo.code}', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting promotion: {str(e)}")
        flash(f'Lỗi khi xóa khuyến mãi: {str(e)}', 'error')
    
    return redirect(url_for('admin_promotions'))

@app.route('/admin/promotions/<int:promo_id>/edit', methods=['POST'])
@admin_required
def admin_edit_promotion(promo_id):
    """Chỉnh sửa khuyến mãi"""
    try:
        promo = Promotion.query.get_or_404(promo_id)
        
        # Cập nhật thông tin
        promo.discount_percent = float(request.form.get('discount_percent'))
        promo.min_amount = float(request.form.get('min_amount', 0))
        promo.start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%d')
        promo.end_date = datetime.strptime(request.form.get('end_date'), '%Y-%m-%d')
        promo.description = request.form.get('description', '')
        
        # Cập nhật max_uses nếu có
        max_uses = request.form.get('max_uses')
        if max_uses:
            promo.max_uses = int(max_uses)
        else:
            promo.max_uses = None
        
        db.session.commit()
        flash(f'Đã cập nhật mã khuyến mãi {promo.code}', 'success')
        
    except Exception as e:
        db.session.rollback()
        print(f"Error editing promotion: {str(e)}")
        flash(f'Lỗi khi chỉnh sửa khuyến mãi: {str(e)}', 'error')
    
    return redirect(url_for('admin_promotions'))

@app.route('/promotions')
def promotions():
    """Trang hiển thị các mã khuyến mãi có sẵn cho khách hàng"""
    # Lấy các promotion đang active và còn hiệu lực
    now = datetime.now()
    active_promotions = Promotion.query.filter(
        Promotion.active == True,
        Promotion.start_date <= now,
        Promotion.end_date >= now
    ).order_by(Promotion.discount_percent.desc()).all()
    
    return render_template('promotions.html', promotions=active_promotions, now=now)

# ===== QUẢN LÝ ĐỊA ĐIỂM =====
@app.route('/admin/locations')
@admin_required
def admin_locations():
    """Quản lý địa điểm"""
    locations = Location.query.all()
    
    # Tính stats
    stats = {
        'total': len(locations),
        'total_hotels': sum(len(loc.hotels) for loc in locations),
        'cities': len(set(loc.city for loc in locations if loc.city)),
        'most_hotels': max((len(loc.hotels) for loc in locations), default=0)
    }
    
    return render_template('admin/locations.html', locations=locations, stats=stats)


@app.route('/admin/locations/add', methods=['POST'])
@admin_required
def admin_add_location():
    """Thêm địa điểm mới"""
    try:
        name = request.form.get('name')
        city = request.form.get('city')
        description = request.form.get('description', '')
        
        # Xử lý upload ảnh (tùy chọn)
        image_url = None
        if 'image' in request.files:
            image_file = request.files['image']
            if image_file.filename:
                # TODO: Lưu ảnh vào thư mục static hoặc upload lên cloud
                # Tạm thời để trống hoặc dùng URL mặc định
                image_url = f'/static/images/locations/{image_file.filename}'
        
        new_location = Location(
            name=name,
            city=city,
            description=description,
            image=image_url
        )
        
        db.session.add(new_location)
        db.session.commit()
        
        flash(f'Đã thêm địa điểm {name}', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"Error adding location: {str(e)}")
        flash(f'Lỗi khi thêm địa điểm: {str(e)}', 'error')
    
    return redirect(url_for('admin_locations'))


@app.route('/admin/locations/<int:location_id>/edit', methods=['POST'])
@admin_required
def admin_edit_location(location_id):
    """Chỉnh sửa địa điểm"""
    try:
        location = Location.query.get_or_404(location_id)
        
        location.name = request.form.get('name')
        location.city = request.form.get('city')
        location.description = request.form.get('description', '')
        
        # Xử lý upload ảnh mới (nếu có)
        if 'image' in request.files:
            image_file = request.files['image']
            if image_file.filename:
                # TODO: Lưu ảnh
                location.image = f'/static/images/locations/{image_file.filename}'
        
        db.session.commit()
        flash(f'Đã cập nhật địa điểm {location.name}', 'success')
        
    except Exception as e:
        db.session.rollback()
        print(f"Error editing location: {str(e)}")
        flash(f'Lỗi khi chỉnh sửa địa điểm: {str(e)}', 'error')
    
    return redirect(url_for('admin_locations'))


@app.route('/admin/locations/<int:location_id>/delete', methods=['POST'])
@admin_required
def admin_delete_location(location_id):
    """Xóa địa điểm"""
    try:
        location = Location.query.get_or_404(location_id)
        
        # Kiểm tra xem có khách sạn nào không
        if location.hotels:
            flash(f'Không thể xóa địa điểm {location.name} vì còn {len(location.hotels)} khách sạn', 'warning')
            return redirect(url_for('admin_locations'))
        
        db.session.delete(location)
        db.session.commit()
        
        flash(f'Đã xóa địa điểm {location.name}', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting location: {str(e)}")
        flash(f'Lỗi khi xóa địa điểm: {str(e)}', 'error')
    
    return redirect(url_for('admin_locations'))

@app.route('/booking/<int:booking_id>')
@login_required
def booking_detail(booking_id):
    """Xem chi tiết đặt phòng"""
    booking = Booking.query.get_or_404(booking_id)
    
    # Kiểm tra quyền: chỉ admin hoặc chủ booking mới xem được
    if not current_user.is_admin and booking.user_id != current_user.id:
        flash('Bạn không có quyền xem đặt phòng này', 'danger')
        return redirect(url_for('my_bookings'))
    
    return render_template('booking_detail.html', booking=booking)
if __name__ == '__main__':
    
   # init_db() 
    app.run(debug=True, port=5000)