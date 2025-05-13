"""
Database Connector Module - Handles data storage for OPC-UA values
Adapted for specific process_data table structure
"""
import logging
import mariadb
import configparser
import datetime
import sys
import time
import threading

logger = logging.getLogger(__name__)


class DbConnector:
    """Database connector for storing OPC-UA values in MariaDB"""

    def __init__(self, config_file='config.ini'):
        """Initialize the database connection"""
        self.config = configparser.ConfigParser()
        self.config.read(config_file)

        self.conn = None
        self.cursor = None
        self.connected = False
        self.config_file = config_file

        # Current values cache with mapping to db fields
        self.current_values = {}
        self.opc_to_db_mapping = {}

        # Logging control
        self.logging_active = False
        self.logging_thread = None
        self.shutdown_event = threading.Event()

        # Initialize default config if needed
        self.init_config()

    def init_config(self):
        """Ensure database section exists in config"""
        if 'DATABASE' not in self.config:
            logger.warning("No DATABASE section in config.ini, creating default")
            self.config['DATABASE'] = {
                'enabled': 'false',
                'host': 'localhost',
                'user': 'opcua_user',
                'password': 'password',
                'database': 'opcua_gateway',
                'log_interval': '60'  # seconds
            }
            with open(self.config_file, 'w') as f:
                self.config.write(f)

    def connect(self):
        """Connect to the database"""
        if not self.config.getboolean('DATABASE', 'enabled', fallback=False):
            logger.info("Database logging disabled in config")
            return False

        try:
            self.conn = mariadb.connect(
                host=self.config.get('DATABASE', 'host', fallback='localhost'),
                user=self.config.get('DATABASE', 'user', fallback='opcua_user'),
                password=self.config.get('DATABASE', 'password', fallback='password'),
                database=self.config.get('DATABASE', 'database', fallback='opcua_gateway')
            )
            self.cursor = self.conn.cursor()
            self.connected = True
            logger.info("Connected to database successfully")

            # Load tag mappings from database
            self._load_tag_mappings()

            # Start logging thread if enabled
            self._start_logging_thread()

            return True
        except mariadb.Error as e:
            logger.error(f"Error connecting to database: {e}")
            self.connected = False
            return False

    def _load_tag_mappings(self):
        """Load OPC tag to database field mappings"""
        try:
            self.cursor.execute("SELECT opc_tag_name, db_field_name FROM tagnames")
            mappings = self.cursor.fetchall()

            self.opc_to_db_mapping = {tag: field for tag, field in mappings}
            logger.info(f"Loaded {len(self.opc_to_db_mapping)} tag mappings from database")
        except mariadb.Error as e:
            logger.error(f"Error loading tag mappings: {e}")
            self.opc_to_db_mapping = {}

    def _start_logging_thread(self):
        """Start the background logging thread"""
        if not self.config.getboolean('DATABASE', 'enabled', fallback=False):
            return

        log_interval = self.config.getint('DATABASE', 'log_interval', fallback=60)
        if log_interval <= 0:
            logger.warning("Invalid log interval, setting to 60 seconds")
            log_interval = 60

        self.shutdown_event.clear()
        self.logging_thread = threading.Thread(target=self._logging_worker, args=(log_interval,))
        self.logging_thread.daemon = True
        self.logging_thread.start()
        self.logging_active = True
        logger.info(f"Database logging thread started with interval of {log_interval} seconds")

    def _logging_worker(self, interval):
        """Background worker that logs data at specified intervals"""
        while not self.shutdown_event.is_set():
            try:
                if self.connected and self.current_values:
                    self._log_current_values()
            except Exception as e:
                logger.error(f"Error in logging thread: {e}")

            # Sleep but check for shutdown periodically
            for _ in range(interval):
                if self.shutdown_event.is_set():
                    break
                time.sleep(1)

    def _log_current_values(self):
        """Log current values to process_data table"""
        if not self.connected or not self.current_values:
            return

        try:
            # Build INSERT query dynamically based on available values
            field_names = ["timestamp"]
            placeholders = ["NOW()"]
            values = []

            # Map OPC tags to database fields and collect values
            for tag_name, value_dict in self.current_values.items():
                if tag_name in self.opc_to_db_mapping:
                    db_field = self.opc_to_db_mapping[tag_name]
                    field_names.append(db_field)
                    placeholders.append("?")
                    values.append(value_dict["value"])

            if len(field_names) <= 1:  # Only timestamp, no actual data
                return

            # Construct and execute query
            query = f"INSERT INTO process_data ({', '.join(field_names)}) VALUES ({', '.join(placeholders)})"
            self.cursor.execute(query, values)
            self.conn.commit()
            logger.debug(f"Logged {len(values)} process values to database")

        except mariadb.Error as e:
            logger.error(f"Error logging to process_data table: {e}")
            # Try to reconnect
            self._try_reconnect()

    def _try_reconnect(self):
        """Try to reconnect to database after an error"""
        try:
            logger.info("Attempting database reconnection")
            self.disconnect()
            time.sleep(2)  # Brief pause before retry
            return self.connect()
        except Exception as e:
            logger.error(f"Reconnection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect from database"""
        # Stop logging thread
        if self.logging_active:
            self.shutdown_event.set()
            if self.logging_thread and self.logging_thread.is_alive():
                self.logging_thread.join(timeout=5.0)
            self.logging_active = False

        # Close database connection
        if self.conn:
            try:
                self.conn.close()
                logger.info("Disconnected from database")
            except mariadb.Error as e:
                logger.error(f"Error disconnecting from database: {e}")
            finally:
                self.conn = None
                self.cursor = None
                self.connected = False

    def update_value(self, tag_name, value, unit, timestamp):
        """Update current value for a tag"""
        self.current_values[tag_name] = {
            "value": value,
            "unit": unit,
            "timestamp": timestamp
        }

    def log_event(self, event_type, message, severity="info"):
        """Log an event to the event_log table"""
        if not self.connected:
            return False

        try:
            query = "INSERT INTO event_log (timestamp, event_type, message, severity) VALUES (NOW(), ?, ?, ?)"
            self.cursor.execute(query, (event_type, message, severity))
            self.conn.commit()
            return True
        except mariadb.Error as e:
            logger.error(f"Error logging event: {e}")
            return False

    def get_field_history(self, field_name, hours=24, limit=1000):
        """Get historical data for a specific database field"""
        if not self.connected:
            return []

        try:
            query = f"""
                SELECT timestamp, {field_name} 
                FROM process_data 
                WHERE timestamp > DATE_SUB(NOW(), INTERVAL ? HOUR)
                AND {field_name} IS NOT NULL
                ORDER BY timestamp 
                LIMIT ?
            """
            self.cursor.execute(query, (hours, limit))
            return self.cursor.fetchall()
        except mariadb.Error as e:
            logger.error(f"Error retrieving field history: {e}")
            return []

    def get_tag_history(self, tag_name, hours=24, limit=1000):
        """Get historical data for a specific OPC tag using mapping"""
        # Find the database field for this tag
        field_name = self.opc_to_db_mapping.get(tag_name)
        if not field_name:
            logger.warning(f"No database field mapping found for tag: {tag_name}")
            return []

        # Get history using the mapped field name
        history_data = self.get_field_history(field_name, hours, limit)

        # Find the unit from current values
        unit = self.current_values.get(tag_name, {}).get("unit", "")

        # Format results with the unit
        result = []
        for timestamp, value in history_data:
            result.append((value, unit, timestamp))

        return result