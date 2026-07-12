#!/usr/bin/env python3
"""TicketFlow launcher. Usage: python3 run.py

Environment (all optional):
  PORT              port to listen on (default 8000)
  ADMIN_PASSWORD    organiser dashboard password (default 'admin123')
  STRIPE_SECRET_KEY Stripe TEST secret key to enable real test payments
  TICKETFLOW_DB     path to the SQLite file (default ./ticketflow.db)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from server import main  # noqa: E402

if __name__ == "__main__":
    main()
