"""
Unity Connector Module - HTTP API Server for OPC-UA data
"""
import logging
import datetime
import threading
from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.serving import make_server

logger = logging.getLogger(__name__)


class UnityConnector:
    """HTTP API Gateway for OPC UA values - connects to Unity"""

    def __init__(self, opcua_connector, config=None):
        """Initialize the connector with configuration"""
        # HTTP server settings
        if config is None:
            # Standardverdier hvis ingen config gis
            self.host = '0.0.0.0'
            self.port = 5000
            self.cors_enabled = True
        else:
            # Hent fra config
            self.host = config.get('HTTP', 'host', fallback='0.0.0.0')
            self.port = config.getint('HTTP', 'port', fallback=5000)
            self.cors_enabled = config.getboolean('HTTP', 'cors_enabled', fallback=True)

        # Store reference to OPC-UA connector
        self.opcua = opcua_connector

        # Database connection reference (will be set later if database is enabled)
        self.db = None

        # Global data store
        self.latest_values = {}

        # Server objects
        self.app = Flask(__name__)
        self.server = None
        self.server_thread = None
        self.running = False

        # Register API routes
        self._setup_routes()

    def set_db_connector(self, db_connector):
        """Set the database connector for historical data"""
        self.db = db_connector

    def on_value_update(self, name, value, unit, timestamp):
        """Callback for OPC UA value updates"""
        self.latest_values[name] = {
            "value": value,
            "unit": unit,
            "timestamp": timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp)
        }
        logger.debug(f"Updated value: {name} = {value} {unit}")

    def _setup_routes(self):
        """Set up Flask API routes"""

        # Define API routes
        @self.app.route('/api/values', methods=['GET'])
        def get_all_values():
            """Return all current values"""
            return jsonify(self.latest_values)

        @self.app.route('/api/value/<name>', methods=['GET'])
        def get_value(name):
            """Return a specific value by name"""
            if name in self.latest_values:
                return jsonify(self.latest_values[name])
            return jsonify({"error": "Value not found"}), 404

        @self.app.route('/api/status', methods=['GET'])
        def get_status():
            """Return server status"""
            return jsonify({
                "opcua_connected": self.opcua.connected,
                "nodes_monitoring": len(self.opcua.nodes_to_monitor),
                "values_count": len(self.latest_values),
                "server_time": datetime.datetime.now().isoformat()
            })

        @self.app.route('/api/history/<name>', methods=['GET'])
        def get_history(name):
            """Return historical data for a tag"""
            if not self.db or not self.db.connected:
                return jsonify({"error": "Database not available"}), 503

            # Get hours parameter from query string, default to 24
            hours = request.args.get('hours', default=24, type=int)

            history = self.db.get_tag_history(name, hours)
            if not history:
                return jsonify({"error": "No history found or tag does not exist"}), 404

            # Format the data for the response
            result = []
            for value, unit, timestamp in history:
                result.append({
                    "value": value,
                    "unit": unit,
                    "timestamp": timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp)
                })

            return jsonify(result)

    def start(self):
        """Start the HTTP server"""
        try:
            # Enable CORS if configured
            if self.cors_enabled:
                CORS(self.app)
                logger.info("CORS support enabled")

            # Register callbacks for OPC UA value updates
            self.opcua.add_value_callback(self.on_value_update)

            # Start Flask server in a separate thread
            self.server = make_server(self.host, self.port, self.app)
            self.server_thread = threading.Thread(target=self.server.serve_forever)
            self.server_thread.daemon = True
            self.server_thread.start()

            self.running = True
            logger.info(f"HTTP API server started at http://{self.host}:{self.port}")
            logger.info("Unity can now connect to this endpoint")
            return True

        except Exception as e:
            logger.error(f"Error starting HTTP server: {e}")
            self.stop()
            return False

    def stop(self):
        """Stop the HTTP server"""
        self.running = False

        # Stop HTTP server
        if self.server:
            self.server.shutdown()
            if self.server_thread and self.server_thread.is_alive():
                self.server_thread.join(timeout=1.0)

        logger.info("Unity HTTP server stopped")