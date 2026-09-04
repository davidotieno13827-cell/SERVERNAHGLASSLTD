import csv
import io
import os
import secrets
import textwrap
import uuid
import shutil
from datetime import datetime, timedelta, timezone

try:
    import win32print
except ImportError:
    win32print = None

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from flask import (
    Flask,
    Response,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm, CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash
from wtforms import DecimalField, HiddenField, IntegerField, PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)

app = Flask(__name__)
secret_path = os.path.join(INSTANCE_DIR, "secret.key")
if os.getenv("SECRET_KEY"):
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
elif os.path.exists(secret_path):
    with open(secret_path, "r", encoding="ascii") as secret_file:
        app.config["SECRET_KEY"] = secret_file.read().strip()
else:
    app.config["SECRET_KEY"] = secrets.token_hex(32)
    with open(secret_path, "w", encoding="ascii") as secret_file:
        secret_file.write(app.config["SECRET_KEY"])
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{os.path.join(INSTANCE_DIR, 'app.db')}",
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH", "2097152"))
app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FORCE_HTTPS", "false").lower() == "true"
app.config["WTF_CSRF_ENABLED"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
BUSINESS_TIMEZONE = timezone(timedelta(hours=3), name="EAT")
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "SERVERNAH GLASS LIMITED")
BUSINESS_ADDRESS = os.getenv("BUSINESS_ADDRESS", "HOMABAY, RODI")
BUSINESS_PHONE = os.getenv("BUSINESS_PHONE", "0714868988")
BUSINESS_EMAIL = os.getenv("BUSINESS_EMAIL", "SERVERNAHGLASSLTD@GMAIL.COM")
BUSINESS_WEBSITE = os.getenv("BUSINESS_WEBSITE", "")
BUSINESS_TAX_ID = os.getenv("BUSINESS_TAX_ID", "")
REGISTER_NUMBER = os.getenv("REGISTER_NUMBER", "Till 1")
RETURN_POLICY = os.getenv("RETURN_POLICY", "Returns and exchanges accepted within 7 days with a valid receipt.")
PRINTER_NAME = os.getenv("PRINTER_NAME", "Xprinter XP-80")

csrf = CSRFProtect(app)
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "60 per hour"],
    storage_uri=os.getenv("RATE_LIMIT_STORAGE_URI", "memory://"),
)


@app.template_filter("business_datetime")
def format_business_datetime(value, date_format="%Y-%m-%d %H:%M:%S"):
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BUSINESS_TIMEZONE).strftime(date_format)


@app.context_processor
def receipt_business_details():
    return {
        "business_name": BUSINESS_NAME,
        "business_address": BUSINESS_ADDRESS,
        "business_phone": BUSINESS_PHONE,
        "business_email": BUSINESS_EMAIL,
        "business_website": BUSINESS_WEBSITE,
        "business_tax_id": BUSINESS_TAX_ID,
        "return_policy": RETURN_POLICY,
    }


def print_receipt_to_printer(sale):
    if win32print is None:
        raise RuntimeError("Direct printing requires the pywin32 package.")

    line_width = 42

    def centered(value):
        return value.center(line_width)

    def row(label, value):
        value = str(value)
        available = max(1, line_width - len(label) - 1)
        return "{}{}".format(label.ljust(line_width - min(len(value), available)), value[-available:])

    def centered_lines(value):
        return [centered(line) for line in textwrap.wrap(str(value), width=line_width) or [""]]

    contact = " | ".join(value for value in [BUSINESS_PHONE, BUSINESS_EMAIL, BUSINESS_WEBSITE] if value)
    lines = [
        centered(BUSINESS_NAME),
        centered("Official Sales Receipt"),
        centered(BUSINESS_ADDRESS),
        centered(contact),
        row("Receipt / Invoice #", sale.id),
        row("Date", format_business_datetime(sale.timestamp)),
        row("Cashier", sale.cashier_name or "POS operator"),
        row("Register", sale.register_number),
        row("Customer", sale.customer_name or "Walk-in customer"),
        "-" * line_width,
        row("Item / SKU", "Qty x Unit Price"),
        row(sale.product_name, "{} x KES {:.2f}".format(sale.quantity, sale.price)),
    ]
    if sale.product_sku:
        lines.append(sale.product_sku)
    lines.extend([
        row("Subtotal", "KES {:.2f}".format(sale.total_price + sale.discount_amount)),
        row("Grand Total", "KES {:.2f}".format(sale.total_price)),
        "-" * line_width,
        row("Payment Method", sale.payment_method),
    ])
    if sale.amount_tendered is not None:
        lines.extend([
            row("Amount Tendered", "KES {:.2f}".format(sale.amount_tendered)),
            row("Change", "KES {:.2f}".format(sale.change_amount or 0)),
        ])
    lines.extend(["", *centered_lines(RETURN_POLICY), centered("Thank you for shopping with us."), "", ""])

    printer = win32print.OpenPrinter(PRINTER_NAME)
    try:
        win32print.StartDocPrinter(printer, 1, ("Receipt #{}".format(sale.id), None, "RAW"))
        try:
            win32print.StartPagePrinter(printer)
            data = ("\x1b@" + "\n".join(lines) + "\x1dV\x42\x06").encode("cp437", errors="replace")
            bytes_written = win32print.WritePrinter(printer, data)
            if bytes_written != len(data):
                raise RuntimeError("The printer accepted only part of the receipt.")
            win32print.EndPagePrinter(printer)
        finally:
            win32print.EndDocPrinter(printer)
    finally:
        win32print.ClosePrinter(printer)


def print_combined_receipt_to_printer(sales):
    if not sales:
        raise RuntimeError("There are no items on this receipt.")
    if win32print is None:
        raise RuntimeError("Direct printing requires the pywin32 package.")

    line_width = 42

    def centered(value):
        return str(value).center(line_width)

    def row(label, value):
        value = str(value)
        available = max(1, line_width - len(label) - 1)
        return "{}{}".format(label.ljust(line_width - min(len(value), available)), value[-available:])

    first_sale = sales[0]
    lines = [
        centered(BUSINESS_NAME),
        centered("Official Sales Receipt"),
        centered(BUSINESS_ADDRESS),
        centered(" | ".join(value for value in [BUSINESS_PHONE, BUSINESS_EMAIL, BUSINESS_WEBSITE] if value)),
        row("Receipt / Invoice #", first_sale.id),
        row("Date", format_business_datetime(first_sale.timestamp)),
        row("Cashier", first_sale.cashier_name or "POS operator"),
        row("Register", first_sale.register_number),
        row("Customer", first_sale.customer_name or "Walk-in customer"),
        "-" * line_width,
    ]
    for sale in sales:
        lines.append(row(sale.product_name, "{} x KES {:.2f}".format(sale.quantity, sale.price)))
        if sale.product_sku:
            lines.append("  {}".format(sale.product_sku))
    total = sum(sale.total_price for sale in sales)
    amount_tendered = first_sale.amount_tendered
    lines.extend([
        "-" * line_width,
        row("Grand Total", "KES {:.2f}".format(total)),
        row("Payment Method", first_sale.payment_method),
    ])
    if amount_tendered is not None:
        lines.extend([
            row("Amount Tendered", "KES {:.2f}".format(amount_tendered)),
            row("Change", "KES {:.2f}".format(first_sale.change_amount or 0)),
        ])
    lines.extend(["", *[centered(line) for line in textwrap.wrap(RETURN_POLICY, width=line_width) or [""]], centered("Thank you for shopping with us."), "", ""])

    printer = win32print.OpenPrinter(PRINTER_NAME)
    try:
        win32print.StartDocPrinter(printer, 1, ("Receipt #{}".format(first_sale.id), None, "RAW"))
        try:
            win32print.StartPagePrinter(printer)
            data = ("\x1b@" + "\n".join(lines) + "\x1dV\x42\x06").encode("cp437", errors="replace")
            bytes_written = win32print.WritePrinter(printer, data)
            if bytes_written != len(data):
                raise RuntimeError("The printer accepted only part of the receipt.")
            win32print.EndPagePrinter(printer)
        finally:
            win32print.EndDocPrinter(printer)
    finally:
        win32print.ClosePrinter(printer)


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="cashier")

    @property
    def is_admin(self):
        return self.role == "admin"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), nullable=True)
    address = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    products = db.relationship("Product", backref="supplier", lazy=True)


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    sales = db.relationship("Sale", backref="customer", lazy=True)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(50), unique=True, nullable=True)
    name = db.Column(db.String(120), nullable=False)
    brand = db.Column(db.String(80), nullable=False)
    buying_price = db.Column(db.Float, nullable=False, default=0.0)
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, nullable=False, default=0)
    reserved_stock = db.Column(db.Integer, nullable=False, default=0)
    min_stock_level = db.Column(db.Integer, nullable=False, default=5)
    supplier_id = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=True)
    sales = db.relationship("Sale", backref="product", lazy=True, cascade="all, delete-orphan")
    activity_records = db.relationship("ProductActivity", backref="product", lazy=True, cascade="all, delete-orphan")


class ProductActivity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False, index=True)
    activity_type = db.Column(db.String(20), nullable=False)
    quantity_added = db.Column(db.Integer, nullable=False, default=0)
    stock_after = db.Column(db.Integer, nullable=False)
    buying_price = db.Column(db.Float, nullable=False)
    selling_price = db.Column(db.Float, nullable=False)
    recorded_by = db.Column(db.String(50), nullable=True)
    recorded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    product_sku = db.Column(db.String(50), nullable=True)
    product_name = db.Column(db.String(120), nullable=False)
    price = db.Column(db.Float, nullable=False)
    cost_price = db.Column(db.Float, nullable=False, default=0.0)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    total_price = db.Column(db.Float, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=True)
    customer_name = db.Column(db.String(120), nullable=True)
    discount_amount = db.Column(db.Float, nullable=False, default=0.0)
    tax_amount = db.Column(db.Float, nullable=False, default=0.0)
    payment_method = db.Column(db.String(30), nullable=False, default="Cash")
    payment_reference = db.Column(db.String(100), nullable=True)
    amount_tendered = db.Column(db.Float, nullable=True)
    change_amount = db.Column(db.Float, nullable=True)
    cashier_name = db.Column(db.String(50), nullable=True)
    register_number = db.Column(db.String(50), nullable=False, default="Till 1")
    receipt_token = db.Column(db.String(36), nullable=True, index=True)
    receipt_printed = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    print_attempts = db.Column(db.Integer, nullable=False, default=0)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @property
    def profit(self):
        return self.total_price - (self.cost_price * self.quantity)


class CheckoutRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    request_key = db.Column(db.String(36), nullable=False, unique=True)
    receipt_token = db.Column(db.String(36), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class MetricSnapshot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    captured_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    label = db.Column(db.String(30), nullable=False, default="Daily")
    is_initial = db.Column(db.Boolean, nullable=False, default=False)
    total_stock = db.Column(db.Integer, nullable=False, default=0)
    inventory_value = db.Column(db.Float, nullable=False, default=0.0)
    expected_profit = db.Column(db.Float, nullable=False, default=0.0)
    sales_count = db.Column(db.Integer, nullable=False, default=0)
    total_revenue = db.Column(db.Float, nullable=False, default=0.0)


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=50)])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Login")


class ProductForm(FlaskForm):
    sku = StringField("SKU", validators=[Optional(), Length(max=50)])
    name = StringField("Product Name", validators=[DataRequired(), Length(max=120)])
    brand = StringField("Brand", validators=[DataRequired(), Length(max=80)])
    supplier_id = SelectField("Supplier", coerce=int, validators=[NumberRange(min=1, message="Select the supplier for this product.")])
    buying_price = DecimalField("Buying Price", validators=[DataRequired()])
    price = DecimalField("Selling Price", validators=[DataRequired()])
    stock = IntegerField("Stock", validators=[DataRequired(), NumberRange(min=0)])
    submit = SubmitField("Save")


class RestockForm(FlaskForm):
    quantity = IntegerField("Quantity to Add", validators=[DataRequired(), NumberRange(min=1)])
    buying_price = DecimalField("Buying Price", validators=[DataRequired()])
    price = DecimalField("Selling Price", validators=[DataRequired()])
    submit = SubmitField("Restock")


class QuickSaleForm(FlaskForm):
    product_id = SelectField("Product", coerce=int, validators=[DataRequired()])
    customer_name = StringField("Customer Name", validators=[Optional(), Length(max=120)])
    quantity = IntegerField("Quantity", validators=[DataRequired(), NumberRange(min=1)])
    payment_method = SelectField("Payment Method", choices=[("Cash", "Cash"), ("M-Pesa", "M-Pesa"), ("Card", "Credit/Debit Card")], validators=[DataRequired()])
    amount_tendered = DecimalField("Amount Tendered", validators=[Optional(), NumberRange(min=0)])
    submit = SubmitField("Sell")


class CartAddForm(FlaskForm):
    product_id = SelectField("Product", coerce=int, validators=[DataRequired()])
    quantity = IntegerField("Quantity", validators=[DataRequired(), NumberRange(min=1)])
    submit = SubmitField("Add to cart")


class CartCheckoutForm(FlaskForm):
    checkout_key = HiddenField()
    customer_name = StringField("Customer Name", validators=[Optional(), Length(max=120)])
    payment_method = SelectField("Payment Method", choices=[("Cash", "Cash"), ("M-Pesa", "M-Pesa"), ("Card", "Credit/Debit Card")], validators=[DataRequired()])
    amount_tendered = DecimalField("Amount Tendered", validators=[Optional(), NumberRange(min=0)])
    submit = SubmitField("Complete Sale")


class SupplierForm(FlaskForm):
    name = StringField("Supplier Name", validators=[DataRequired(), Length(max=120)])
    phone = StringField("Phone", validators=[Optional(), Length(max=30)])
    address = StringField("Address", validators=[Optional(), Length(max=200)])
    submit = SubmitField("Save Supplier")


class CustomerForm(FlaskForm):
    name = StringField("Customer Name", validators=[DataRequired(), Length(max=120)])
    phone = StringField("Phone", validators=[Optional(), Length(max=30)])
    submit = SubmitField("Save Customer")


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def create_default_admin():
    username = os.getenv("ADMIN_USERNAME", "admin")
    password = os.getenv("ADMIN_PASSWORD")
    user = User.query.filter_by(username=username).first()
    if not user:
        password = password or secrets.token_urlsafe(12)
        admin = User(username=username, role="admin")
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        credential_path = os.path.join(INSTANCE_DIR, "initial_admin_credentials.txt")
        if not os.path.exists(credential_path):
            with open(credential_path, "w", encoding="ascii") as credential_file:
                credential_file.write("Username: {}\nPassword: {}\n".format(username, password))
    elif user.role != "admin":
        user.role = "admin"
        db.session.commit()


def admin_required(view):
    from functools import wraps

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            flash("Administrator permission is required for this action.", "danger")
            return redirect(url_for("home"))
        return view(*args, **kwargs)
    return wrapped


def get_dashboard_stats():
    total_products = Product.query.count()
    total_stock = db.session.query(db.func.coalesce(db.func.sum(Product.stock), 0)).scalar() or 0
    inventory_value = db.session.query(db.func.coalesce(db.func.sum(Product.stock * Product.price), 0)).scalar() or 0
    expected_profit = db.session.query(
        db.func.coalesce(db.func.sum((Product.price - Product.buying_price) * Product.stock), 0)
    ).scalar() or 0
    sales_count = Sale.query.filter_by(status="printed").count()
    total_revenue = db.session.query(db.func.coalesce(db.func.sum(Sale.total_price), 0)).filter(Sale.status == "printed").scalar() or 0
    total_cost = db.session.query(
        db.func.coalesce(db.func.sum(Sale.cost_price * Sale.quantity), 0)
    ).filter(Sale.status == "printed").scalar() or 0
    gross_profit = total_revenue - total_cost
    current = {
        "total_products": total_products,
        "total_stock": total_stock,
        "inventory_value": inventory_value,
        "expected_profit": expected_profit,
        "sales_count": sales_count,
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "gross_profit": gross_profit,
    }
    initial = MetricSnapshot.query.filter_by(is_initial=True).order_by(MetricSnapshot.captured_at.asc()).first()
    previous = MetricSnapshot.query.order_by(MetricSnapshot.captured_at.desc()).first()
    current["initial"] = initial
    current["previous"] = previous
    current["captured_at"] = previous.captured_at if previous else datetime.utcnow()
    current["changes"] = {
        "total_stock": total_stock - (initial.total_stock if initial else total_stock),
        "inventory_value": inventory_value - (initial.inventory_value if initial else inventory_value),
        "expected_profit": expected_profit - (initial.expected_profit if initial else expected_profit),
        "sales_count": sales_count - (initial.sales_count if initial else sales_count),
        "total_revenue": total_revenue - (initial.total_revenue if initial else total_revenue),
    }
    return current


def capture_metric_snapshot(label="Daily", force=False):
    stats = get_dashboard_stats()
    latest = MetricSnapshot.query.order_by(MetricSnapshot.captured_at.desc()).first()
    if not force and latest and latest.captured_at.date() == datetime.utcnow().date():
        return latest
    snapshot = MetricSnapshot(
        label=label,
        is_initial=not MetricSnapshot.query.filter_by(is_initial=True).first(),
        total_stock=stats["total_stock"],
        inventory_value=stats["inventory_value"],
        expected_profit=stats["expected_profit"],
        sales_count=stats["sales_count"],
        total_revenue=stats["total_revenue"],
    )
    db.session.add(snapshot)
    db.session.commit()
    return snapshot


def ensure_product_schema():
    inspector = db.inspect(db.engine)
    if "product" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("product")}
    if "reserved_stock" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE product ADD COLUMN reserved_stock INTEGER NOT NULL DEFAULT 0"))
    if "buying_price" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE product ADD COLUMN buying_price FLOAT NOT NULL DEFAULT 0.0"))
        for product in Product.query.all():
            product.buying_price = product.price or 0.0
        db.session.commit()


def ensure_sale_schema():
    inspector = db.inspect(db.engine)
    if "sale" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("sale")}
    if "cost_price" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE sale ADD COLUMN cost_price FLOAT NOT NULL DEFAULT 0.0"))
        for sale in Sale.query.all():
            sale.cost_price = 0.0
        db.session.commit()
    if "customer_id" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE sale ADD COLUMN customer_id INTEGER"))
    if "customer_name" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE sale ADD COLUMN customer_name VARCHAR(120)"))
    new_columns = {
        "discount_amount": "FLOAT NOT NULL DEFAULT 0.0",
        "tax_amount": "FLOAT NOT NULL DEFAULT 0.0",
        "payment_method": "VARCHAR(30) NOT NULL DEFAULT 'Cash'",
        "payment_reference": "VARCHAR(100)",
        "amount_tendered": "FLOAT",
        "change_amount": "FLOAT",
        "cashier_name": "VARCHAR(50)",
        "register_number": "VARCHAR(50) NOT NULL DEFAULT 'Till 1'",
        "receipt_token": "VARCHAR(36)",
        "receipt_printed": "BOOLEAN NOT NULL DEFAULT 1",
        "status": "VARCHAR(20) NOT NULL DEFAULT 'printed'",
        "print_attempts": "INTEGER NOT NULL DEFAULT 0",
    }
    for column_name, column_definition in new_columns.items():
        if column_name not in columns:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE sale ADD COLUMN {column_name} {column_definition}"))


def ensure_user_schema():
    inspector = db.inspect(db.engine)
    if "user" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("user")}
    if "role" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE user ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'cashier'"))


def backup_database():
    database_path = os.path.join(INSTANCE_DIR, "app.db")
    if not os.path.exists(database_path):
        return
    backup_dir = os.path.join(INSTANCE_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(backup_dir, "app-{}.db".format(datetime.now().strftime("%Y%m%d-%H%M%S")))
    if not os.path.exists(backup_path):
        shutil.copy2(database_path, backup_path)
    backups = [
        os.path.join(backup_dir, name)
        for name in os.listdir(backup_dir)
        if name.endswith(".db") and os.path.exists(os.path.join(backup_dir, name))
    ]
    backups.sort(key=os.path.getmtime)
    for old_backup in backups[:-30]:
        os.remove(old_backup)


def ensure_supplier_schema():
    inspector = db.inspect(db.engine)
    if "supplier" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("supplier")}
    if "phone" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE supplier ADD COLUMN phone VARCHAR(30)"))
    if "address" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE supplier ADD COLUMN address VARCHAR(200)"))


def ensure_customer_schema():
    inspector = db.inspect(db.engine)
    if "customer" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("customer")}
    if "phone" not in columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE customer ADD COLUMN phone VARCHAR(30)"))


def seed_product_history():
    for product in Product.query.all():
        if not ProductActivity.query.filter_by(product_id=product.id).first():
            db.session.add(ProductActivity(
                product_id=product.id,
                activity_type="opening balance",
                quantity_added=product.stock,
                stock_after=product.stock,
                buying_price=product.buying_price,
                selling_price=product.price,
                recorded_by="System",
            ))
    db.session.commit()


@app.before_request
def setup_default_admin():
    with app.app_context():
        db.create_all()
        ensure_product_schema()
        ensure_sale_schema()
        ensure_supplier_schema()
        ensure_customer_schema()
        seed_product_history()
        ensure_user_schema()
        create_default_admin()
        backup_database()
        capture_metric_snapshot()


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("6 per minute")
def login():
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        password = form.password.data
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            flash("Welcome back to Savannah Glassmart POS.", "success")
            return redirect(request.args.get("next") or url_for("home"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html", form=form)


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    q = request.args.get("q", "", type=str).strip()
    page = request.args.get("page", 1, type=int)
    low_stock_limit = request.args.get("low_stock", 0, type=int)

    query = Product.query
    if q:
        search_term = "%{}%".format(q)
        query = query.filter(
            (Product.name.ilike(search_term))
            | (Product.brand.ilike(search_term))
            | (Product.sku.ilike(search_term))
        )

    if low_stock_limit:
        query = query.filter(Product.stock <= low_stock_limit)

    products = query.order_by(Product.name.asc()).paginate(page=page, per_page=20, error_out=False)
    quick_sale_form = QuickSaleForm()
    quick_sale_form.product_id.choices = [(product.id, f"{product.name} - {product.brand} ({product.stock} in stock)") for product in Product.query.order_by(Product.name.asc()).all()]
    stats = get_dashboard_stats()
    low_stock_products = Product.query.filter(Product.stock <= Product.min_stock_level).order_by(Product.stock.asc()).all()
    return render_template(
        "home.html",
        products=products,
        quick_sale_form=quick_sale_form,
        stats=stats,
        q=q,
        low_stock_limit=low_stock_limit,
        low_stock_products=low_stock_products,
    )


@app.route("/quick_sell", methods=["POST"])
@login_required
def quick_sell():
    form = QuickSaleForm()
    form.product_id.choices = [(product.id, f"{product.name} - {product.brand}") for product in Product.query.order_by(Product.name.asc()).all()]
    if form.validate_on_submit():
        product = Product.query.get(form.product_id.data)
        if not product:
            flash("Selected product was not found.", "danger")
            return redirect(url_for("home"))

        qty = form.quantity.data
        if qty < 1:
            flash("Quantity must be at least 1.", "danger")
            return redirect(url_for("home"))
        if product.stock - product.reserved_stock < qty:
            flash(f"Not enough available stock for {product.name}. Available: {product.stock - product.reserved_stock}", "danger")
            return redirect(url_for("home"))
        amount_tendered = float(form.amount_tendered.data) if form.amount_tendered.data is not None else None
        total = product.price * qty
        if form.payment_method.data == "Cash" and amount_tendered is not None and amount_tendered < total:
            flash(f"Amount tendered is KES {amount_tendered:.2f}, but the total is KES {total:.2f}. Please top up KES {total - amount_tendered:.2f}.", "danger")
            return redirect(url_for("home"))

        customer = None
        customer_name = "Walk-in customer"
        entered_customer_name = form.customer_name.data.strip() if form.customer_name.data else ""
        if entered_customer_name:
            customer = Customer.query.filter(
                db.func.lower(Customer.name) == entered_customer_name.lower()
            ).first()
            if not customer:
                customer = Customer(name=entered_customer_name)
                db.session.add(customer)
                db.session.flush()
            customer_name = customer.name

        sale = create_sale_record(
            product,
            qty,
            customer=customer,
            customer_name=customer_name,
            payment_method=form.payment_method.data,
            amount_tendered=amount_tendered,
        )
        db.session.commit()
        flash(f"Sold {qty} {product.name} successfully.", "success")
        return redirect(url_for("receipt", sale_id=sale.id))

    for field, errors in form.errors.items():
        for error in errors:
            flash(f"{field}: {error}", "danger")
    return redirect(url_for("home"))


def product_choices():
    return [(product.id, f"{product.name} - {product.brand} ({product.stock} in stock)") for product in Product.query.order_by(Product.name.asc()).all()]


def get_cart_items():
    cart = session.get("cart", {})
    items = []
    for product_id, quantity in cart.items():
        product = db.session.get(Product, int(product_id))
        available = product.stock - product.reserved_stock if product else 0
        if product and quantity > 0:
            items.append({"product": product, "quantity": int(quantity), "total": product.price * int(quantity), "available": available})
    return items


@app.route("/cart/add", methods=["POST"])
@login_required
def add_to_cart():
    form = CartAddForm()
    form.product_id.choices = product_choices()
    if form.validate_on_submit():
        product = db.session.get(Product, form.product_id.data)
        if not product:
            flash("Selected product was not found.", "danger")
        elif product.stock - product.reserved_stock < form.quantity.data:
            flash(f"Not enough available stock for {product.name}. Available: {product.stock - product.reserved_stock}", "danger")
        else:
            cart = session.get("cart", {})
            key = str(product.id)
            new_quantity = int(cart.get(key, 0)) + form.quantity.data
            if new_quantity > product.stock - product.reserved_stock:
                flash(f"Cart quantity for {product.name} cannot exceed available stock ({product.stock - product.reserved_stock}).", "danger")
            else:
                cart[key] = new_quantity
                session["cart"] = cart
                session.setdefault("checkout_key", str(uuid.uuid4()))
                flash(f"Added {form.quantity.data} {product.name} to the cart.", "success")
    else:
        flash("Choose a product and enter a valid quantity.", "danger")
    return redirect(url_for("cart"))


@app.route("/cart")
@login_required
def cart():
    add_form = CartAddForm()
    add_form.product_id.choices = product_choices()
    checkout_form = CartCheckoutForm()
    session.setdefault("checkout_key", str(uuid.uuid4()))
    checkout_form.checkout_key.data = session["checkout_key"]
    items = get_cart_items()
    return render_template("cart.html", add_form=add_form, checkout_form=checkout_form, items=items, cart_total=sum(item["total"] for item in items))


@app.route("/cart/remove/<int:product_id>", methods=["POST"])
@login_required
def remove_from_cart(product_id):
    cart = session.get("cart", {})
    cart.pop(str(product_id), None)
    session["cart"] = cart
    return redirect(url_for("cart"))


@app.route("/cart/checkout", methods=["POST"])
@login_required
def checkout_cart():
    form = CartCheckoutForm()
    items = get_cart_items()
    if not items:
        flash("Add at least one product before completing a sale.", "danger")
        return redirect(url_for("cart"))
    if not form.validate_on_submit():
        flash("Please check the checkout details.", "danger")
        return redirect(url_for("cart"))
    request_key = form.checkout_key.data or session.get("checkout_key")
    if not request_key or request_key != session.get("checkout_key"):
        flash("This checkout form has already been submitted. Please review the current cart.", "warning")
        return redirect(url_for("cart"))
    cart_total = sum(item["total"] for item in items)
    amount_tendered = float(form.amount_tendered.data) if form.amount_tendered.data is not None else None
    if form.payment_method.data == "Cash" and amount_tendered is not None and amount_tendered < cart_total:
        shortfall = cart_total - amount_tendered
        flash(f"Amount tendered is KES {amount_tendered:.2f}, but the total is KES {cart_total:.2f}. Please top up KES {shortfall:.2f}.", "danger")
        return redirect(url_for("cart"))
    for item in items:
        if item["product"].stock - item["product"].reserved_stock < item["quantity"]:
            flash(f"Not enough available stock for {item['product'].name}.", "danger")
            return redirect(url_for("cart"))

    customer_name = form.customer_name.data.strip() if form.customer_name.data else "Walk-in customer"
    customer = None
    if customer_name != "Walk-in customer":
        customer = Customer.query.filter(db.func.lower(Customer.name) == customer_name.lower()).first()
        if not customer:
            customer = Customer(name=customer_name)
            db.session.add(customer)
            db.session.flush()

    receipt_token = str(uuid.uuid4())
    sales = []
    try:
        db.session.add(CheckoutRequest(request_key=request_key, receipt_token=receipt_token))
        db.session.flush()
        for index, item in enumerate(items):
            sales.append(create_sale_record(
                item["product"],
                item["quantity"],
                customer=customer,
                customer_name=customer_name,
                payment_method=form.payment_method.data,
                amount_tendered=amount_tendered if index == 0 else None,
                change_total=cart_total if index == 0 else None,
                receipt_token=receipt_token,
            ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        existing_request = CheckoutRequest.query.filter_by(request_key=request_key).first()
        if existing_request:
            session.pop("cart", None)
            session.pop("checkout_key", None)
            return redirect(url_for("combined_receipt", receipt_token=existing_request.receipt_token))
        flash("The cart could not be saved. No stock was reserved.", "danger")
        return redirect(url_for("cart"))
    session.pop("cart", None)
    session.pop("checkout_key", None)
    flash(f"Sold {len(sales)} product(s) successfully.", "success")
    return redirect(url_for("combined_receipt", receipt_token=receipt_token))


@app.route("/sell/<int:product_id>", methods=["POST"])
@login_required
def sell_product(product_id):
    product = Product.query.get_or_404(product_id)
    qty = request.form.get("quantity", type=int, default=1)
    customer_id = request.form.get("customer_id", type=int)
    customer_name = request.form.get("customer_name", default="Walk-in customer")
    if qty < 1:
        flash("Quantity must be at least 1.", "danger")
        return redirect(url_for("home"))
    if product.stock - product.reserved_stock < qty:
        flash(f"Not enough available stock for {product.name}. Available: {product.stock - product.reserved_stock}", "danger")
        return redirect(url_for("home"))

    customer = None
    if customer_id:
        customer = Customer.query.get(customer_id)
        if customer:
            customer_name = customer.name

    sale = create_sale_record(product, qty, customer=customer, customer_name=customer_name)
    db.session.commit()
    flash(f"Sold {qty} unit(s) of {product.name}.", "success")
    return redirect(url_for("receipt", sale_id=sale.id))


def create_sale_record(
    product,
    quantity,
    customer=None,
    customer_name="Walk-in customer",
    payment_method="Cash",
    amount_tendered=None,
    change_total=None,
    receipt_token=None,
):
    if product.stock - product.reserved_stock < quantity:
        raise ValueError(f"Not enough available stock for {product.name}.")
    product.reserved_stock += quantity
    total = product.price * quantity
    sale = Sale(
        product_id=product.id,
        product_sku=product.sku,
        product_name=product.name,
        price=product.price,
        cost_price=product.buying_price,
        quantity=quantity,
        total_price=total,
        customer_id=customer.id if customer else None,
        customer_name=customer_name,
        payment_method=payment_method,
        amount_tendered=amount_tendered,
        change_amount=amount_tendered - (change_total if change_total is not None else total) if amount_tendered is not None else None,
        cashier_name=current_user.username if current_user.is_authenticated else None,
        register_number=REGISTER_NUMBER,
        receipt_token=receipt_token,
        status="pending",
        timestamp=datetime.utcnow(),
    )
    db.session.add(sale)
    return sale


def finalize_printed_sales(sales):
    for sale in sales:
        if sale.receipt_printed or sale.status == "cancelled":
            continue
        product = db.session.get(Product, sale.product_id)
        if not product or product.stock < sale.quantity:
            raise RuntimeError(f"Not enough stock to finalize {sale.product_name}.")
        product.stock -= sale.quantity
        product.reserved_stock = max(0, product.reserved_stock - sale.quantity)
        sale.status = "printed"
        sale.receipt_printed = True
    db.session.commit()
    capture_metric_snapshot(label="Sale completed", force=True)


def validate_printable_sales(sales):
    for sale in sales:
        if sale.receipt_printed:
            continue
        if sale.status not in ("pending", "failed"):
            raise RuntimeError("This receipt is already being printed or has been completed.")
        product = db.session.get(Product, sale.product_id)
        if not product or product.stock < sale.quantity:
            raise RuntimeError(f"Not enough stock to print {sale.product_name}.")


def claim_sales_for_printing(sales):
    sale_ids = [sale.id for sale in sales if not sale.receipt_printed]
    if not sale_ids:
        return
    updated = Sale.query.filter(
        Sale.id.in_(sale_ids),
        Sale.status.in_(["pending", "failed"]),
        Sale.receipt_printed.is_(False),
    ).update(
        {"status": "printing", "print_attempts": Sale.print_attempts + 1},
        synchronize_session=False,
    )
    if updated != len(sale_ids):
        db.session.rollback()
        raise RuntimeError("This receipt has already been printed or is being printed.")
    db.session.commit()


def mark_print_failed(sales):
    for sale in sales:
        if not sale.receipt_printed:
            sale.status = "failed"
    db.session.commit()


@app.route("/add", methods=["GET", "POST"])
@admin_required
def add_product():
    form = ProductForm()
    form.supplier_id.choices = [(supplier.id, supplier.name) for supplier in Supplier.query.order_by(Supplier.name.asc()).all()]
    form.supplier_id.choices.insert(0, (0, "Select supplier"))
    if form.validate_on_submit():
        sku = form.sku.data.strip() if form.sku.data else None
        if sku:
            existing = Product.query.filter_by(sku=sku).first()
            if existing:
                flash("SKU already exists. Please use a unique SKU or leave it blank.", "danger")
                return render_template("add_product.html", form=form)
        supplier = None
        if form.supplier_id.data not in (None, 0, ""):
            supplier = Supplier.query.get(form.supplier_id.data)
        product = Product(
            sku=sku,
            name=form.name.data.strip(),
            brand=form.brand.data.strip(),
            supplier=supplier,
            buying_price=float(form.buying_price.data),
            price=float(form.price.data),
            stock=int(form.stock.data),
            min_stock_level=5,
        )
        db.session.add(product)
        db.session.commit()
        db.session.add(ProductActivity(
            product_id=product.id,
            activity_type="addition",
            quantity_added=product.stock,
            stock_after=product.stock,
            buying_price=product.buying_price,
            selling_price=product.price,
            recorded_by=current_user.username,
        ))
        db.session.commit()
        flash("Product added successfully.", "success")
        if product.price < product.buying_price:
            flash(f"Warning: selling price KES {product.price:.2f} is below buying price KES {product.buying_price:.2f}. This product will make a loss.", "warning")
        return redirect(url_for("home"))
    return render_template("add_product.html", form=form)


@app.route("/edit/<int:product_id>", methods=["GET", "POST"])
@admin_required
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)
    form = ProductForm(obj=product)
    form.supplier_id.choices = [(supplier.id, supplier.name) for supplier in Supplier.query.order_by(Supplier.name.asc()).all()]
    form.supplier_id.choices.insert(0, (0, "Select supplier"))
    if form.validate_on_submit():
        sku = form.sku.data.strip() if form.sku.data else None
        if sku and Product.query.filter(Product.id != product.id, Product.sku == sku).first():
            flash("SKU must be unique for each product.", "danger")
            return render_template("edit_product.html", form=form, product=product)
        supplier = None
        if form.supplier_id.data not in (None, 0, ""):
            supplier = Supplier.query.get(form.supplier_id.data)
        product.sku = sku
        product.name = form.name.data.strip()
        product.brand = form.brand.data.strip()
        product.supplier = supplier
        product.buying_price = float(form.buying_price.data)
        product.price = float(form.price.data)
        product.stock = int(form.stock.data)
        db.session.commit()
        flash("Product updated successfully.", "success")
        if product.price < product.buying_price:
            flash(f"Warning: selling price KES {product.price:.2f} is below buying price KES {product.buying_price:.2f}. This product will make a loss.", "warning")
        return redirect(url_for("home"))
    return render_template("edit_product.html", form=form, product=product)


@app.route("/restock/<int:product_id>", methods=["GET", "POST"])
@admin_required
def restock_product(product_id):
    product = Product.query.get_or_404(product_id)
    form = RestockForm(obj=product)
    if form.validate_on_submit():
        quantity = int(form.quantity.data)
        product.buying_price = float(form.buying_price.data)
        product.price = float(form.price.data)
        product.stock += quantity
        db.session.add(ProductActivity(
            product_id=product.id,
            activity_type="restock",
            quantity_added=quantity,
            stock_after=product.stock,
            buying_price=product.buying_price,
            selling_price=product.price,
            recorded_by=current_user.username,
        ))
        db.session.commit()
        flash(f"Restocked {product.name} by {quantity} units and recorded the updated prices.", "success")
        if product.price < product.buying_price:
            flash(f"Warning: selling price KES {product.price:.2f} is below buying price KES {product.buying_price:.2f}. This product will make a loss.", "warning")
        return redirect(url_for("home"))
    return render_template("restock_product.html", form=form, product=product)


@app.route("/delete/<int:product_id>", methods=["POST"])
@admin_required
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    flash(f"{product.name} was deleted successfully.", "success")
    return redirect(url_for("home"))


@app.route("/product/<int:product_id>/history")
@admin_required
def product_history(product_id):
    product = Product.query.get_or_404(product_id)
    activities = ProductActivity.query.filter_by(product_id=product.id).order_by(ProductActivity.recorded_at.desc()).all()
    return render_template("product_history.html", product=product, activities=activities)


@app.route("/history")
@login_required
def sales_history():
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    q = request.args.get("q", "", type=str).strip()
    page = request.args.get("page", 1, type=int)

    query = Sale.query.filter(Sale.status == "printed")
    if start_date:
        query = query.filter(Sale.timestamp >= datetime.strptime(start_date, "%Y-%m-%d"))
    if end_date:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        query = query.filter(Sale.timestamp < end_dt.replace(hour=23, minute=59, second=59))
    if q:
        term = "%{}%".format(q)
        query = query.filter((Sale.product_name.ilike(term)) | (Sale.product_sku.ilike(term)))

    sales = query.order_by(Sale.timestamp.desc()).paginate(page=page, per_page=15, error_out=False)
    return render_template("sales_history.html", sales=sales, start_date=start_date, end_date=end_date, q=q)


@app.route("/pending-sales")
@login_required
def pending_sales():
    pending = Sale.query.filter(Sale.status.in_(["pending", "failed", "printing"])).order_by(Sale.timestamp.asc()).all()
    groups = []
    grouped = {}
    for sale in pending:
        key = sale.receipt_token or str(sale.id)
        if key not in grouped:
            grouped[key] = {"token": sale.receipt_token, "sales": [], "total": 0}
            groups.append(grouped[key])
        grouped[key]["sales"].append(sale)
        grouped[key]["total"] += sale.total_price
    return render_template("pending_sales.html", groups=groups)


@app.route("/pending-sales/<int:sale_id>/cancel", methods=["POST"])
@login_required
def cancel_pending_sale(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    if sale.status not in ("pending", "failed"):
        flash("Only pending or failed receipts can be cancelled.", "danger")
        return redirect(url_for("pending_sales"))
    sales = Sale.query.filter_by(receipt_token=sale.receipt_token).all() if sale.receipt_token else [sale]
    for grouped_sale in sales:
        if grouped_sale.status not in ("pending", "failed"):
            continue
        product = db.session.get(Product, grouped_sale.product_id)
        if product:
            product.reserved_stock = max(0, product.reserved_stock - grouped_sale.quantity)
        grouped_sale.status = "cancelled"
    db.session.commit()
    flash("Pending sale cancelled and stock reservation released.", "info")
    return redirect(url_for("pending_sales"))


@app.route("/reports")
@login_required
def reports():
    stats = get_dashboard_stats()
    recent_sales = Sale.query.filter_by(status="printed").order_by(Sale.timestamp.desc()).limit(5).all()
    top_profit_products = db.session.query(
        Product.name,
        db.func.sum((Product.price - Product.buying_price) * Product.stock).label("potential_profit")
    ).group_by(Product.name).order_by(db.func.sum((Product.price - Product.buying_price) * Product.stock).desc()).limit(5).all()
    return render_template("reports.html", stats=stats, recent_sales=recent_sales, top_profit_products=top_profit_products)


@app.route("/progress")
@login_required
def progress_report():
    snapshots = MetricSnapshot.query.order_by(MetricSnapshot.captured_at.desc()).limit(100).all()
    initial = MetricSnapshot.query.filter_by(is_initial=True).order_by(MetricSnapshot.captured_at.asc()).first()
    return render_template("progress.html", snapshots=snapshots, initial=initial)


@app.route("/suppliers", methods=["GET", "POST"])
@login_required
def suppliers():
    form = SupplierForm()
    if form.validate_on_submit():
        supplier = Supplier(
            name=form.name.data.strip(),
            phone=form.phone.data.strip() if form.phone.data else None,
            address=form.address.data.strip() if form.address.data else None,
        )
        db.session.add(supplier)
        db.session.commit()
        flash("Supplier saved successfully.", "success")
        return redirect(url_for("suppliers"))
    all_suppliers = Supplier.query.order_by(Supplier.name.asc()).all()
    return render_template("suppliers.html", form=form, suppliers=all_suppliers)


@app.route("/customers", methods=["GET", "POST"])
@login_required
def customers():
    form = CustomerForm()
    if form.validate_on_submit():
        customer = Customer(
            name=form.name.data.strip(),
            phone=form.phone.data.strip() if form.phone.data else None,
        )
        db.session.add(customer)
        db.session.commit()
        flash("Customer saved successfully.", "success")
        return redirect(url_for("customers"))
    all_customers = Customer.query.order_by(Customer.name.asc()).all()
    return render_template("customers.html", form=form, customers=all_customers)


@app.route("/sales-summary")
@login_required
def sales_summary():
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    query = db.session.query(
        db.func.date(Sale.timestamp).label("sale_date"),
        db.func.sum(Sale.total_price).label("revenue"),
        db.func.sum(Sale.cost_price * Sale.quantity).label("cost"),
        db.func.sum(Sale.quantity).label("items_sold"),
    )
    if start_date:
        query = query.filter(Sale.timestamp >= datetime.strptime(start_date, "%Y-%m-%d"))
    if end_date:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        query = query.filter(Sale.timestamp < end_dt.replace(hour=23, minute=59, second=59))
    summary = query.filter(Sale.status == "printed").group_by(db.func.date(Sale.timestamp)).order_by(db.func.date(Sale.timestamp).desc()).all()
    return render_template("sales_summary.html", summary=summary, start_date=start_date, end_date=end_date)


@app.route("/receipt/<int:sale_id>")
@login_required
def receipt(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    if sale.receipt_token:
        return redirect(url_for("combined_receipt", receipt_token=sale.receipt_token))
    return render_template("receipt.html", sale=sale)


@app.route("/receipt/<int:sale_id>/print", methods=["POST"])
@login_required
def print_receipt(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    try:
        if sale.receipt_printed:
            print_receipt_to_printer(sale)
            flash("Receipt reprinted successfully.", "success")
            return redirect(url_for("receipt", sale_id=sale.id))
        validate_printable_sales([sale])
        claim_sales_for_printing([sale])
        print_receipt_to_printer(sale)
        finalize_printed_sales([sale])
        flash("Receipt sent to the printer.", "success")
    except Exception as error:
        if sale.status == "printing":
            mark_print_failed([sale])
        flash("Receipt could not be printed: {}".format(error), "danger")
    return redirect(url_for("receipt", sale_id=sale.id))


@app.route("/receipt/batch/<receipt_token>")
@login_required
def combined_receipt(receipt_token):
    sales = Sale.query.filter_by(receipt_token=receipt_token).order_by(Sale.id.asc()).all()
    if not sales:
        return "Receipt not found", 404
    return render_template("receipt.html", sales=sales, sale=sales[0], combined=True)


@app.route("/receipt/batch/<receipt_token>/print", methods=["POST"])
@login_required
def print_combined_receipt(receipt_token):
    sales = Sale.query.filter_by(receipt_token=receipt_token).order_by(Sale.id.asc()).all()
    if not sales:
        return "Receipt not found", 404
    try:
        if all(sale.receipt_printed for sale in sales):
            print_combined_receipt_to_printer(sales)
            flash("Receipt reprinted successfully.", "success")
            return redirect(url_for("combined_receipt", receipt_token=receipt_token))
        validate_printable_sales(sales)
        claim_sales_for_printing(sales)
        print_combined_receipt_to_printer(sales)
        finalize_printed_sales(sales)
        flash("Receipt sent to the printer.", "success")
    except Exception as error:
        if any(sale.status == "printing" for sale in sales):
            mark_print_failed(sales)
        flash("Receipt could not be printed: {}".format(error), "danger")
    return redirect(url_for("combined_receipt", receipt_token=receipt_token))


@app.route("/receipt/<int:sale_id>/mark-printed", methods=["POST"])
@login_required
def mark_receipt_printed(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    sales = Sale.query.filter_by(receipt_token=sale.receipt_token).all() if sale.receipt_token else [sale]
    try:
        validate_printable_sales(sales)
        finalize_printed_sales(sales)
        flash("Receipt marked as printed and stock finalized.", "success")
    except Exception as error:
        flash("Receipt could not be finalized: {}".format(error), "danger")
    return redirect(url_for("combined_receipt", receipt_token=sale.receipt_token) if sale.receipt_token else url_for("receipt", sale_id=sale.id))


@app.route("/export/inventory")
@login_required
def export_inventory():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["sku", "name", "brand", "buying_price", "price", "stock", "min_stock_level"])
    for product in Product.query.order_by(Product.name).all():
        writer.writerow([
            product.sku or "",
            product.name,
            product.brand,
            product.buying_price,
            product.price,
            product.stock,
            product.min_stock_level,
        ])
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=inventory.csv"})


@app.route("/export/sales")
@login_required
def export_sales():
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    q = request.args.get("q", "", type=str).strip()

    query = Sale.query.filter(Sale.status == "printed")
    if start_date:
        query = query.filter(Sale.timestamp >= datetime.strptime(start_date, "%Y-%m-%d"))
    if end_date:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        query = query.filter(Sale.timestamp < end_dt.replace(hour=23, minute=59, second=59))
    if q:
        term = "%{}%".format(q)
        query = query.filter((Sale.product_name.ilike(term)) | (Sale.product_sku.ilike(term)))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "product_sku", "product_name", "buying_price", "selling_price", "quantity", "total_price", "gross_profit", "timestamp"])
    for sale in query.order_by(Sale.timestamp.desc()).all():
        writer.writerow([
            sale.id,
            sale.product_sku or "",
            sale.product_name,
            sale.cost_price,
            sale.price,
            sale.quantity,
            sale.total_price,
            sale.profit,
            sale.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        ])
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=sales_history.csv"})


@app.route("/import", methods=["GET", "POST"])
@admin_required
def import_csv():
    if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("Please choose a CSV file.", "danger")
            return redirect(url_for("import_csv"))

        try:
            raw = file.read().decode("utf-8-sig")
            rows = list(csv.DictReader(io.StringIO(raw)))
        except Exception as exc:
            flash(f"Unable to read CSV file: {exc}", "danger")
            return redirect(url_for("import_csv"))

        imported = 0
        updated = 0
        skipped = 0
        low_price_count = 0

        for row in rows:
            if not row:
                continue
            mapped = {
                "sku": row.get("sku") or row.get("code") or "",
                "name": row.get("name") or row.get("product_name") or "",
                "brand": row.get("brand") or row.get("make") or "",
                "buying_price": row.get("buying_price") or row.get("cost_price") or row.get("purchase_price") or "",
                "price": row.get("price") or row.get("unit_price") or "",
                "stock": row.get("stock") or row.get("quantity") or row.get("stock_quantity") or "",
            }
            if not mapped["name"].strip() or not mapped["brand"].strip():
                skipped += 1
                continue
            try:
                buying_price = float(str(mapped["buying_price"]).replace(",", "")) if mapped["buying_price"] else 0.0
                price = float(str(mapped["price"]).replace(",", ""))
                qty = int(float(str(mapped["stock"]).replace(",", "")))
            except ValueError:
                skipped += 1
                continue

            sku_value = mapped["sku"].strip() if mapped["sku"] else None
            product = None
            if sku_value:
                product = Product.query.filter_by(sku=sku_value).first()
            if not product:
                product = Product.query.filter_by(name=mapped["name"].strip(), brand=mapped["brand"].strip()).first()

            if product:
                product.buying_price = buying_price if buying_price else product.buying_price
                product.price = price
                if price < product.buying_price:
                    low_price_count += 1
                product.stock += qty
                if sku_value and not product.sku:
                    product.sku = sku_value
                updated += 1
            else:
                new_product = Product(
                    sku=sku_value,
                    name=mapped["name"].strip(),
                    brand=mapped["brand"].strip(),
                    buying_price=buying_price,
                    price=price,
                    stock=qty,
                    min_stock_level=5,
                )
                db.session.add(new_product)
                if price < buying_price:
                    low_price_count += 1
                imported += 1

        db.session.commit()
        flash(
            f"Import complete: {imported} imported, {updated} updated, {skipped} skipped.",
            "success",
        )
        if low_price_count:
            flash(f"Warning: {low_price_count} imported product(s) have a selling price below their buying price.", "warning")
        return redirect(url_for("home"))

    return render_template("import_csv.html")


@app.route("/health")
def health():
    return {"status": "ok", "app": "Savannah Glassmart POS"}


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        create_default_admin()
    app.run(host="127.0.0.1", port=5000, debug=False)
