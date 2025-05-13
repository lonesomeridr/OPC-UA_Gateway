"""
OPC UA to Unity HTTP Gateway
Simple HTTP API server that exposes OPC UA values to Unity
"""
import logging
import threading
import time
import signal
import sys
from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.serving import make_server
import configparser
from opcua_connector import OpcUaConnector

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

# Create Flask app
app = Flask(__name__)

# Global data store
latest_values = {}


class UnityConnector:
    """HTTP API Gateway for OPC UA values - connects to Unity"""

    def __init__(self, config_file='config.ini'):
        """Initialize the connector with configuration"""
        # Load configuration
        self.config = configparser.ConfigParser()
        self.config.read(config_file)

        # HTTP server settings
        self.host = self.config.get('HTTP', 'host', fallback='0.0.0.0')
        self.port = self.config.getint('HTTP', 'port', fallback=5000)
        self.cors_enabled = self.config.getboolean('HTTP', 'cors_enabled', fallback=True)

        # OPC UA connector
        self.opcua = OpcUaConnector(config_file)

        # Server objects
        self.server = None
        self.server_thread = None
        self.running = False

    def on_value_update(self, name, value, unit, timestamp):
        """Callback for OPC UA value updates"""
        global latest_values
        latest_values[name] = {
            "value": value,
            "unit": unit,
            "timestamp": timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp)
        }
        logger.debug(f"Updated value: {name} = {value} {unit}")

    def start(self):
        """Start the HTTP server and OPC UA connection"""
        try:
            # Enable CORS if configured
            if self.cors_enabled:
                CORS(app)
                logger.info("CORS support enabled")

            # Connect to OPC UA server
            if not self.opcua.connect():
                logger.error("Failed to connect to OPC UA server")
                return False

            # Register value update callback
            self.opcua.add_value_callback(self.on_value_update)

            # Subscribe to nodes
            if not self.opcua.subscribe_to_nodes():
                logger.error("Failed to subscribe to OPC UA nodes")
                self.opcua.disconnect()
                return False

            # Start Flask server in a separate thread
            self.server = make_server(self.host, self.port, app)
            self.server_thread = threading.Thread(target=self.server.serve_forever)
            self.server_thread.daemon = True
            self.server_thread.start()

            self.running = True
            logger.info(f"HTTP API server started at http://{self.host}:{self.port}")
            logger.info("Unity can now connect to this endpoint")
            return True

        except Exception as e:
            logger.error(f"Error starting connector: {e}")
            self.stop()
            return False

    def stop(self):
        """Stop the HTTP server and OPC UA connection"""
        self.running = False

        # Stop HTTP server
        if self.server:
            self.server.shutdown()
            if self.server_thread and self.server_thread.is_alive():
                self.server_thread.join(timeout=1.0)

        # Disconnect OPC UA
        if hasattr(self, 'opcua'):
            self.opcua.disconnect()

        logger.info("Connector stopped")


# Define API routes
@app.route('/api/values', methods=['GET'])
def get_all_values():
    """Return all current values"""
    global latest_values
    return jsonify(latest_values)


@app.route('/api/value/<name>', methods=['GET'])
def get_value(name):
    """Return a specific value by name"""
    global latest_values
    if name in latest_values:
        return jsonify(latest_values[name])
    return jsonify({"error": "Value not found"}), 404


def signal_handler(sig, frame):
    """Handle Ctrl+C to stop the script gracefully"""
    logger.info("Stopping connector...")
    if 'connector' in globals() and connector.running:
        connector.stop()
    sys.exit(0)


if __name__ == "__main__":
    # Register signal handler for Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Create and start connector
    connector = UnityConnector()
    if connector.start():
        try:
            # Keep the main thread running
            while connector.running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping due to keyboard interrupt...")
        finally:
            connector.stop()
    else:
        logger.error("Failed to start connector")
        sys.exit(1)