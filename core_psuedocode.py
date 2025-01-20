import zipfile
import io
from PIL import Image
import numpy as np
import logging
import tempfile
import os
import requests
from enum import Enum
from datetime import datetime
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from concurrent.futures import ThreadPoolExecutor
import subprocess

logging.basicConfig(level=logging.INFO)

# just using concurrent futures here bc the database inserts aren't complex.
# I am hardcoding here - that would never happen in a production or dev environment.

# assume postgres server is already running.

def create_database_and_user(hardcode):
    try:
        conn = psycopg2.connect(
            dbname='postgres',
            user='postgres',
            password='postgres',
            host=hardcode['host'],
            port=hardcode['port']
        )
        conn.autocommit = True
        cur = conn.cursor()
        
        # does it exist?
        cur.execute("SELECT 1 FROM pg_database WHERE datname = 'preymaker'")
        if not cur.fetchone():
            cur.execute("CREATE DATABASE preymaker;")
            logging.info("Database created successfully.")
        
        # creating a user as an example..
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'preymaker'")
        if not cur.fetchone():
            cur.execute("CREATE USER preymaker WITH PASSWORD 'preymaker';")
            cur.execute("GRANT ALL PRIVILEGES ON DATABASE preymaker TO preymaker;")
            logging.info("User created and privileges granted successfully.")
            
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error creating database and user: {e}")
        raise

def read_contents(hardcode):
    try:
        conn = psycopg2.connect(**hardcode)
        cur = conn.cursor()
        cur.execute("SELECT * FROM file_names;")
        rows = cur.fetchall()
        for row in rows:
            print(row)
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error querying database: {e}")

# ------------------------ #
# this is the python example of a bash script of mine sans ffmpeg
# what we are doing is unzipping a large zip file, and as each png is generated, we are overlaying that png
# to a blended.png and then deleting the original immediately afterwards.
# you could just put this on runtime memory but I'm using temps

class Blender:
    def __init__(self, zip_path, output_path, blend_amount=0.5, size=(500, 500), hardcode=None):
        self.zip_path = zip_path
        self.output_path = output_path
        self.blend_amount = blend_amount
        self.size = size
        self.temp_path = None
        self.executor = ThreadPoolExecutor(max_workers=5)
        self.connection_pool = None
        # just call setup here
        self.setup_database(hardcode)

    def setup_database(self, hardcode):
        try:
            # i'm threading so lets just create a connection pool
            # +1 to thread safety
            self.connection_pool = SimpleConnectionPool(
                minconn=1,
                maxconn=10,
                **hardcode
            )
    
            conn = self.connection_pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS file_names (
                            id SERIAL PRIMARY KEY,
                            file_name TEXT NOT NULL,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                conn.commit()
                logging.info("Database table setup completed successfully")
            finally:
                self.connection_pool.putconn(conn)
        except Exception as e:
            logging.error(f"Error setting up database: {e}")
            raise

    # avoiding some nonetype errors and confirming that this db is threadable.
    def store_file_name(self, file_name):
        conn = None
        try:
            conn = self.connection_pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO file_names (file_name) VALUES (%s) RETURNING id", 
                    (file_name,)
                )
                inserted_id = cur.fetchone()[0]
                conn.commit()
                logging.info(f"Successfully inserted file {file_name} with ID {inserted_id}")
        except Exception as e:
            logging.error(f"Error storing file name {file_name}: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                self.connection_pool.putconn(conn)
    
    # resize with pillow & convert to numpy array
    def get_frames(self):
        with zipfile.ZipFile(self.zip_path) as zip_ref:
            for name in zip_ref.namelist():
                # not needed but this incase you reuse this script
                if not name.endswith('.png'):
                    continue
                try:
                    img_data = zip_ref.read(name)
                    img = Image.open(io.BytesIO(img_data))
                    img = img.resize(self.size)
                    frame = np.array(img).astype(float)
                    print(f"frame -> {name}")
                    # Submit the store_file_name task to the executor
                    future = self.executor.submit(self.store_file_name, name)
                    yield frame
                except Exception as e:
                    logging.error(f"Error processing {name}: {e}")
    
    def blend_pics(self):
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            self.temp_path = tmp.name
            first = True
            
            for frame in self.get_frames():
                if first:
                    Image.fromarray(frame.astype('uint8')).save(self.temp_path)
                    first = False
                else:
                    # previous results & blend
                    result = np.array(Image.open(self.temp_path)).astype(float)
                    # using a simple weighted average
                    result = result * (1 - self.blend_amount) + frame * self.blend_amount
                    Image.fromarray(result.astype('uint8')).save(self.temp_path)
            
            os.replace(self.temp_path, self.output_path)
            print(f"saved -> {self.output_path}")
    
    def __del__(self):
        if self.executor:
            self.executor.shutdown(wait=True)
        if self.connection_pool:
            self.connection_pool.closeall()

## ------------------------ ##

## just grabbing delay data from the mta public api
## i'm only using the lines I use on a daily basis & I'm colorizing them
## also I only care about the active alerts

class MTA(Enum):
    A = '\033[94m'  # Blue
    C = '\033[94m'  # Blue
    F = '\033[91m'  # Orange
    G = '\033[92m'  # Light Green
    L = '\033[95m'  # Gray
    S = '\033[97m'  # Light Gray
    Q = '\033[93m'  # Yellow

    def colorize(self, text: str) -> str:
        return f'{self.value}{text}\033[0m'

class SubwayJson:
    def __init__(self):
        self.url = 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts.json'
        self.trains = ['A', 'C', 'F', 'G', 'L', 'S', 'Q']
        self.printed_keys = set()

    def get_json(self):
        response = requests.get(self.url)
        return response.json()
    
    def is_alert_active(self, alert):
        current_time = int(datetime.now().timestamp())
        for period in alert.get('active_period', []):
            if period.get('start', 0) <= current_time <= period.get('end', float('inf')):
                return True
        return False

    def get_delays(self):
        data = self.get_json()
        for train in self.trains:
            for item in data['entity']:
                alert = item.get('alert', {})
                if self.is_alert_active(alert):
                    for informed_entity in alert.get('informed_entity', []):
                        if informed_entity.get('route_id') == train:
                            message = f"{train}: {alert['header_text']['translation'][0]['text']}"
                            print(MTA[train].colorize(message))
                            break

    # some process code that ultimately wasn't needed - was used to better read / understand the json

        # def list_route_ids(self, data=None, parent_key=''):
        #     if data is None:
        #         data = self.get_json()
        #     keys = []
        #     if isinstance(data, dict):
        #         for key, value in data.items():
        #             full_key = f"{parent_key}.{key}" if parent_key else key
        #             if key == 'route_id' and value in self.trains and full_key not in self.printed_keys:
        #                 print(f"{full_key}: {value}")
        #                 self.printed_keys.add(full_key)
        #                 keys.append(full_key)
        #             keys.extend(self.list_route_ids(value, full_key))
        #     elif isinstance(data, list):
        #         for index, item in enumerate(data):
        #             full_key = f"{parent_key}[{index}]"
        #             keys.extend(self.list_route_ids(item, full_key))
        #     return keys

## if you need to download the zip files, just run grab.sh please!
if __name__ == "__main__":
    zip_path = 'annotations/stuff_val2017_pixelmaps.zip'
    json_path = 'annotations/image_info_test2014.json'

    # bad idea to hardcode this, but it's just a demo
    hardcode = {"dbname": "preymaker", "user": "preymaker", "password": "preymaker","host": "localhost", "port": "5432"}
    start_postgresql()
    create_database_and_user(hardcode)

    blender = Blender(zip_path, 'blended.png', blend_amount=0.5)
    blender.blend_pics()

    subway_json = SubwayJson()
    subway_json.get_delays()

    read_contents(hardcode)