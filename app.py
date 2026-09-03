import csv
import io
import os
import textwrap
import uuid
from datetime import datetime, timedelta, timezone

try:
    import win32print
except ImportError:
    win32print = None

from dotenv import load_dotenv
from sqlalchemy import text
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
from wtforms import DecimalField, IntegerField, PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "save_secret_key")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{os.path.join(INSTANCE_DIR, 'app.db')}",
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH", "2097152"))
app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FORCE_HTTPS", "false").lower() == "true"
app.config["WTF_CSRF_ENABLED"] = True
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
            win32print.WritePrinter(printer, data)
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
            win32print.WritePrinter(printer, data)
            win32print.EndPagePrinter(printer)
        finally:
            win32print.EndDocPrinter(printer)
    finally:
        win32print.ClosePrinter(printer)


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

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
    min_stock_level = db.Column(db.Integer, nullable=False, default=5)
    supplier_id = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=True)
    sales = db.relationship("Sale", backref="product", lazy=True, cascade="all, delete-orphan")


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
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @property
    def profit(self):
        return self.total_price - (self.cost_price * self.quantity)


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
    password = os.getenv("ADMIN_PASSWORD", "password123")
    user = User.query.filter_by(username=username).first()
    if not user:
        admin = User(username=username)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()


def get_dashboard_stats():
    total_products = Product.query.count()
    total_stock = db.session.query(db.func.coalesce(db.func.sum(Product.stock), 0)).scalar() or 0
    inventory_value = db.session.query(db.func.coalesce(db.func.sum(Product.stock * Product.price), 0)).scalar() or 0
    expected_profit = db.session.query(
        db.func.coalesce(db.func.sum((Product.price - Product.buying_price) * Product.stock), 0)
    ).scalar() or 0
    sales_count = Sale.query.count()
    total_revenue = db.session.query(db.func.coalesce(db.func.sum(Sale.total_price), 0)).scalar() or 0
    total_cost = db.session.query(
        db.func.coalesce(db.func.sum(Sale.cost_price * Sale.quantity), 0)
    ).scalar() or 0
    gross_profit = total_revenue - total_cost
    return {
        "total_products": total_products,
        "total_stock": total_stock,
        "inventory_value": inventory_value,
        "expected_profit": expected_profit,
        "sales_count": sales_count,
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "gross_profit": gross_profit,
    }


def ensure_product_schema():
    inspector = db.inspect(db.engine)
    if "product" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("product")}
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
    }
    for column_name, column_definition in new_columns.items():
        if column_name not in columns:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE sale ADD COLUMN {column_name} {column_definition}"))


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


@app.before_request
def setup_default_admin():
    with app.app_context():
        db.create_all()
        ensure_product_schema()
        ensure_sale_schema()
        ensure_supplier_schema()
        ensure_customer_schema()
        create_default_admin()


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
        if product.stock < qty:
            flash(f"Not enough stock for {product.name}. Available: {product.stock}", "danger")
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
            amount_tendered=float(form.amount_tendered.data) if form.amount_tendered.data is not None else None,
        )
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
        if product and quantity > 0:
            items.append({"product": product, "quantity": int(quantity), "total": product.price * int(quantity)})
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
        elif product.stock < form.quantity.data:
            flash(f"Not enough stock for {product.name}. Available: {product.stock}", "danger")
        else:
            cart = session.get("cart", {})
            key = str(product.id)
            new_quantity = int(cart.get(key, 0)) + form.quantity.data
            if new_quantity > product.stock:
                flash(f"Cart quantity for {product.name} cannot exceed stock ({product.stock}).", "danger")
            else:
                cart[key] = new_quantity
                session["cart"] = cart
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
    for item in items:
        if item["product"].stock < item["quantity"]:
            flash(f"Not enough stock for {item['product'].name}. Available: {item['product'].stock}", "danger")
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
    amount_tendered = float(form.amount_tendered.data) if form.amount_tendered.data is not None else None
    cart_total = sum(item["total"] for item in items)
    sales = []
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
    session.pop("cart", None)
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
    if product.stock < qty:
        flash(f"Not enough stock for {product.name}. Available: {product.stock}", "danger")
        return redirect(url_for("home"))

    customer = None
    if customer_id:
        customer = Customer.query.get(customer_id)
        if customer:
            customer_name = customer.name

    sale = create_sale_record(product, qty, customer=customer, customer_name=customer_name)
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
    product.stock -= quantity
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
        timestamp=datetime.utcnow(),
    )
    db.session.add(sale)
    db.session.commit()
    return sale


@app.route("/add", methods=["GET", "POST"])
@login_required
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
        flash("Product added successfully.", "success")
        return redirect(url_for("home"))
    return render_template("add_product.html", form=form)


@app.route("/edit/<int:product_id>", methods=["GET", "POST"])
@login_required
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
        return redirect(url_for("home"))
    return render_template("edit_product.html", form=form, product=product)


@app.route("/restock/<int:product_id>", methods=["GET", "POST"])
@login_required
def restock_product(product_id):
    product = Product.query.get_or_404(product_id)
    form = RestockForm()
    if form.validate_on_submit():
        product.stock += int(form.quantity.data)
        db.session.commit()
        flash(f"Restocked {product.name} by {form.quantity.data} units.", "success")
        return redirect(url_for("home"))
    return render_template("restock_product.html", form=form, product=product)


@app.route("/delete/<int:product_id>", methods=["POST"])
@login_required
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    flash(f"{product.name} was deleted successfully.", "success")
    return redirect(url_for("home"))


@app.route("/history")
@login_required
def sales_history():
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    q = request.args.get("q", "", type=str).strip()
    page = request.args.get("page", 1, type=int)

    query = Sale.query
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


@app.route("/reports")
@login_required
def reports():
    stats = get_dashboard_stats()
    recent_sales = Sale.query.order_by(Sale.timestamp.desc()).limit(5).all()
    top_profit_products = db.session.query(
        Product.name,
        db.func.sum((Product.price - Product.buying_price) * Product.stock).label("potential_profit")
    ).group_by(Product.name).order_by(db.func.sum((Product.price - Product.buying_price) * Product.stock).desc()).limit(5).all()
    return render_template("reports.html", stats=stats, recent_sales=recent_sales, top_profit_products=top_profit_products)


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
    summary = query.group_by(db.func.date(Sale.timestamp)).order_by(db.func.date(Sale.timestamp).desc()).all()
    return render_template("sales_summary.html", summary=summary, start_date=start_date, end_date=end_date)


@app.route("/receipt/<int:sale_id>")
@login_required
def receipt(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    return render_template("receipt.html", sale=sale)


@app.route("/receipt/<int:sale_id>/print", methods=["POST"])
@login_required
def print_receipt(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    try:
        print_receipt_to_printer(sale)
        flash("Receipt sent to the printer.", "success")
    except Exception as error:
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
        print_combined_receipt_to_printer(sales)
        flash("Receipt sent to the printer.", "success")
    except Exception as error:
        flash("Receipt could not be printed: {}".format(error), "danger")
    return redirect(url_for("combined_receipt", receipt_token=receipt_token))


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

    query = Sale.query
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
@login_required
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
                imported += 1

        db.session.commit()
        flash(
            f"Import complete: {imported} imported, {updated} updated, {skipped} skipped.",
            "success",
        )
        return redirect(url_for("home"))

    return render_template("import_csv.html")


@app.route("/health")
def health():
    return {"status": "ok", "app": "Savannah Glassmart POS"}


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        create_default_admin()
    app.run(host="0.0.0.0", port=5000, debug=True)
