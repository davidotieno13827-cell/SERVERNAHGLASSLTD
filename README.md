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
5. Open http://localhost:5000 and log in with:
   - Username: admin
   - Password: password123

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

The database is stored locally at `instance/app.db`. Back up that file regularly
to a USB drive or another safe location. The POS does not require GitHub,
hosting, email, or internet payment services for local sales and printing.
