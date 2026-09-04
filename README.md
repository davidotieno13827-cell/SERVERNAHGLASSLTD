# Savannah Glassmart POS

A lightweight Flask-based point-of-sale system for a glass and hardware retail shop.

## Features
- Admin login/logout
- Inventory dashboard with stock metrics
- Product add, edit, restock, and delete
- Quick sales and individual product sales
- Sales history with filters and CSV export
- Inventory CSV import
- Inventory exports
- Low-stock alerts

## Multi-product sale

1. Open **Multi-product Sale** from the POS navigation or click **Choose Multiple Products** on the home screen.
2. Select a product, enter its quantity, and click **Add to cart**. Repeat for each product.
3. Review the cart, enter the customer and payment details, then click **Complete Sale and View Receipt**.
4. Click **Print Receipt** to print all products on one receipt.

Stock is reduced only after the receipt is successfully sent to the printer. A pending or failed receipt does not reduce inventory.

## Product history

Each new product addition and restock is recorded against the product with its
date/time, quantity added, stock after the change, buying price, selling price,
and operator. Open **History** beside a product in Inventory to review its
records. Restocking also updates and records the current buying and selling
prices.
Product forms and CSV imports warn when a selling price is below its buying
price because that sale would create a loss.

## Quick start

1. Create a virtual environment
   ```bash
   python -m venv .venv
   ```
2. Activate it
   ```bash
   .venv\Scripts\activate
   ```
3. Install dependencies
   ```bash
   pip install -r requirements.txt
   ```
4. Run the app
   ```bash
   python run.py
   ```
5. Open http://localhost:5000. On a new installation, the generated administrator credentials are saved in `instance/initial_admin_credentials.txt`.

## Default environment
Copy `.env.example` to `.env` and adjust values as needed.

## Single-shop offline setup

The POS is designed to run on the shop computer without internet access:

1. Copy the whole project folder to the shop computer.
2. Install Python 3.11 or newer on that computer.
3. Open PowerShell in the project folder and run:
   ```powershell
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   .venv\Scripts\python.exe run.py
   ```
4. Open `http://127.0.0.1:5000` in the browser on the shop computer.
5. Install the Xprinter XP-Q80A Windows driver and connect it by USB.

The database is stored locally at `instance/app.db`. The POS creates consistent
timestamped SQLite snapshots in `instance/backups/` and never automatically
deletes old backups. Copy those backups to a USB drive or another computer
regularly; local backups cannot protect against disk failure, theft, or ransomware.
Products with sales or history cannot be deleted, protecting those records.
Pending receipts can be retried or cancelled from
**Pending Receipts**. Stock is reserved for pending sales and is finalized only
after printing or an explicit **Printed Manually** confirmation.

Cashiers can sell and print receipts. Administrator permission is required to
add, edit, restock, delete, or import products. The POS does not require
GitHub, hosting, email, or internet payment services for local sales and printing.
