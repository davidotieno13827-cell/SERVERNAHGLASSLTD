import os
import unittest

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

    def test_quick_sale_opens_its_receipt(self):
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
        self.assertIn("/receipt/2?auto_print=1", response.headers["Location"])


if __name__ == "__main__":
    unittest.main()
