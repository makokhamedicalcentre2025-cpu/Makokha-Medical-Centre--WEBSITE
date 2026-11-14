import os


port = int(os.getenv('PORT') or 10000)
bind = f'0.0.0.0:{port}'
worker_class = 'gthread'
workers = 1
threads = 100
