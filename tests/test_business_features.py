import os
import unittest
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import User, app, db, Customer, Product, Sale, Supplier


class BusinessFeatureTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        with app.app_context():
            db.drop_all()
            db.create_all()

            admin = User(username="admin")
            admin.set_password("password123")
            db.session.add(admin)
            db.session.flush()

            supplier = Supplier(name="Glass Hub", phone="0700111222")
            customer = Customer(name="Amina K", phone="0711222333")
            product = Product(
                sku="GL-001",
                name="Tempered Glass",
                brand="ClearView",
                buying_price=500.0,
                price=800.0,
                stock=10,
                min_stock_level=5,
                supplier=supplier,
            )
            db.session.add_all([supplier, customer, product])
            db.session.commit()

            sale = Sale(
                product_id=product.id,
                product_sku=product.sku,
                product_name=product.name,
                price=product.price,
                cost_price=product.buying_price,
                quantity=2,
                total_price=1600.0,
                customer_id=customer.id,
                customer_name=customer.name,
            )
            db.session.add(sale)
            db.session.commit()

    def test_supplier_customer_and_sale_record_are_saved(self):
        with app.app_context():
            self.assertEqual(Supplier.query.count(), 1)
            self.assertEqual(Customer.query.count(), 1)
            self.assertEqual(Product.query.count(), 1)
            self.assertEqual(Sale.query.count(), 1)
            sale = Sale.query.first()
            self.assertEqual(sale.profit, 600.0)

    def test_receipt_page_renders(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = "1"
            session["_fresh"] = True
        response = client.get("/receipt/1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Receipt", response.data)

    def test_bootstrap_assets_are_local(self):
        client = app.test_client()
        response = client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"cdn.jsdelivr.net", response.data)
        self.assertIn(b"/static/bootstrap.min.css", response.data)

    def test_quick_sale_opens_receipt_without_printing(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = "1"
            session["_fresh"] = True
        response = client.post(
            "/quick_sell",
            data={"product_id": "1", "customer_name": "", "quantity": "1", "payment_method": "Cash"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/receipt/2", response.headers["Location"])
        self.assertNotIn("auto_print", response.headers["Location"])

    def test_cart_checkout_creates_one_combined_receipt(self):
        with app.app_context():
            second_product = Product(
                sku="GL-002",
                name="Window Handle",
                brand="ClearView",
                buying_price=100.0,
                price=250.0,
                stock=4,
            )
            db.session.add(second_product)
            db.session.commit()
            second_product_id = second_product.id

        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = "1"
            session["_fresh"] = True
        first = client.post("/cart/add", data={"product_id": "1", "quantity": "2"})
        second = client.post("/cart/add", data={"product_id": str(second_product_id), "quantity": "1"})
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        response = client.post(
            "/cart/checkout",
            data={"customer_name": "", "payment_method": "Cash", "amount_tendered": "2000"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/receipt/batch/", response.headers["Location"])
        receipt = client.get(response.headers["Location"])
        self.assertEqual(receipt.status_code, 200)
        self.assertIn(b"Tempered Glass", receipt.data)
        self.assertIn(b"Window Handle", receipt.data)
        with app.app_context():
            self.assertEqual(Sale.query.count(), 3)
            self.assertEqual(Product.query.get(1).stock, 10)
            self.assertEqual(Product.query.get(second_product_id).stock, 4)
            self.assertEqual(Product.query.get(1).reserved_stock, 2)
            self.assertEqual(Product.query.get(second_product_id).reserved_stock, 1)
            token = response.headers["Location"].rsplit("/", 1)[-1]
            grouped_sales = Sale.query.filter_by(receipt_token=token).all()
            self.assertEqual(grouped_sales[0].change_amount, 150.0)
            self.assertFalse(grouped_sales[0].receipt_printed)
        with patch("app.print_combined_receipt_to_printer"):
            printed = client.post(f"/receipt/batch/{token}/print", follow_redirects=False)
        self.assertEqual(printed.status_code, 302)
        with app.app_context():
            self.assertEqual(Product.query.get(1).stock, 8)
            self.assertEqual(Product.query.get(second_product_id).stock, 3)
            self.assertTrue(all(sale.receipt_printed for sale in Sale.query.filter_by(receipt_token=token).all()))
            self.assertEqual(Product.query.get(1).reserved_stock, 0)
            self.assertEqual(Product.query.get(second_product_id).reserved_stock, 0)

    def test_quick_sell_rejects_insufficient_cash(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = "1"
            session["_fresh"] = True
        response = client.post(
            "/quick_sell",
            data={"product_id": "1", "customer_name": "", "quantity": "1", "payment_method": "Cash", "amount_tendered": "700"},
            follow_redirects=True,
        )
        self.assertIn(b"Please top up KES 100.00", response.data)
        with app.app_context():
            self.assertEqual(Sale.query.count(), 1)
            self.assertEqual(Product.query.get(1).stock, 10)
            self.assertEqual(Product.query.get(1).reserved_stock, 0)

    def test_cashier_cannot_open_inventory_management(self):
        with app.app_context():
            cashier = User(username="cashier", role="cashier")
            cashier.set_password("cashier-password")
            db.session.add(cashier)
            db.session.commit()
            cashier_id = cashier.id
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(cashier_id)
            session["_fresh"] = True
        response = client.get("/add")
        self.assertEqual(response.status_code, 302)

    def test_failed_print_does_not_reduce_stock(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = "1"
            session["_fresh"] = True
        response = client.post("/quick_sell", data={"product_id": "1", "customer_name": "", "quantity": "1", "payment_method": "Cash"})
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            self.assertEqual(Product.query.get(1).stock, 10)
            sale_id = Sale.query.order_by(Sale.id.desc()).first().id
        with patch("app.print_receipt_to_printer", side_effect=RuntimeError("printer unavailable")):
            printed = client.post(f"/receipt/{sale_id}/print", follow_redirects=False)
        self.assertEqual(printed.status_code, 302)
        with app.app_context():
            self.assertEqual(Product.query.get(1).stock, 10)
            self.assertFalse(db.session.get(Sale, sale_id).receipt_printed)

    def test_cart_checkout_rejects_insufficient_cash(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = "1"
            session["_fresh"] = True
        client.post("/cart/add", data={"product_id": "1", "quantity": "1"})
        response = client.post(
            "/cart/checkout",
            data={"customer_name": "", "payment_method": "Cash", "amount_tendered": "700"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Please top up KES 100.00", response.data)
        with app.app_context():
            self.assertEqual(Sale.query.count(), 1)
            self.assertEqual(Product.query.get(1).stock, 10)


if __name__ == "__main__":
    unittest.main()
