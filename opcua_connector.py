import logging
import os
import time
import datetime
from opcua import Client, ua
import configparser
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)


class OpcUaConnector:
    """
    Manages connection to OPC UA server and handles data subscriptions
    """

    def __init__(self, config_file='config.ini'):
        """Initialize OPC UA connector with configuration"""
        self.config = configparser.ConfigParser()
        self.config.read(config_file)

        # OPC UA server configuration
        self.endpoint_url = self.config.get('OPCUA', 'server_url')
        self.application_uri = self.config.get('OPCUA', 'application_uri')
        self.security_policy = self.config.get('OPCUA', 'security_policy')
        self.security_mode = self.config.get('OPCUA', 'security_mode')

        # Parse monitoring nodes from config
        self.nodes_to_monitor = []
        monitoring_section = self.config['MONITORING']

        # Find all nodes with pattern node{i}_id
        i = 1
        while f'node{i}_id' in monitoring_section:
            node_id = monitoring_section[f'node{i}_id']
            node_name = monitoring_section[f'node{i}_name']
            node_unit = monitoring_section.get(f'node{i}_unit', '')  # Unit is optional

            self.nodes_to_monitor.append({
                'id': node_id,
                'name': node_name,
                'unit': node_unit
            })

            i += 1

        # OPC UA objects
        self.client = None
        self.subscription = None
        self.handles = []
        self.connected = False

        # Value storage
        self.latest_values = {}
        self.value_callbacks = []

    def add_value_callback(self, callback):
        """
        Register a callback to be called when values change
        The callback will receive (node_name, value, unit, timestamp) as arguments
        """
        self.value_callbacks.append(callback)

    def generate_certificates(self):
        """Generate security certificates with the application URI embedded correctly"""
        try:
            logger.info("Generating certificates...")

            # Create cert directory if it doesn't exist
            cert_dir = os.path.join(os.getcwd(), "certificates")
            if not os.path.exists(cert_dir):
                os.makedirs(cert_dir)

            # Certificate paths (absolute paths)
            cert_path = os.path.join(cert_dir, "certificate.der")
            private_key_path = os.path.join(cert_dir, "private_key.pem")

            # If certificates already exist, use them
            if os.path.exists(cert_path) and os.path.exists(private_key_path):
                logger.info(f"Using existing certificates from {cert_dir}")
                return cert_path, private_key_path

            # Generate private key
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048
            )

            # Write private key to file
            with open(private_key_path, "wb") as f:
                f.write(private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption()
                ))

            # Generate self-signed certificate
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, u"OPC UA Client"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"My Organization"),
                x509.NameAttribute(NameOID.COUNTRY_NAME, u"NO")
            ])

            # Use datetime.UTC for modern Python versions
            try:
                now = datetime.datetime.now(datetime.UTC)
            except AttributeError:
                # For older Python versions
                now = datetime.datetime.utcnow()

            # Include the application URI in the Subject Alternative Name extension
            san = x509.SubjectAlternativeName([
                x509.DNSName(u"localhost"),
                x509.UniformResourceIdentifier(self.application_uri)
            ])

            cert = x509.CertificateBuilder().subject_name(
                subject
            ).issuer_name(
                issuer
            ).public_key(
                private_key.public_key()
            ).serial_number(
                x509.random_serial_number()
            ).not_valid_before(
                now
            ).not_valid_after(
                # Certificate valid for 1 year
                now + datetime.timedelta(days=365)
            ).add_extension(
                san,
                critical=False
            ).sign(private_key, hashes.SHA256())

            # Write certificate to file
            with open(cert_path, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.DER))

            logger.info(f"Certificate generated at: {cert_path}")
            logger.info(f"Private key generated at: {private_key_path}")

            return cert_path, private_key_path
        except Exception as e:
            logger.error(f"Error generating certificates: {e}")
            raise

    def connect(self):
        """Connect to the OPC UA server with the specified security settings"""
        try:
            # Generate certificates
            cert_path, private_key_path = self.generate_certificates()

            # Create client
            self.client = Client(self.endpoint_url)

            # Set security settings
            security_string = f"{self.security_policy},{self.security_mode},{cert_path},{private_key_path}"
            logger.info(f"Setting security with string: {security_string}")
            self.client.set_security_string(security_string)

            # Set application URI
            self.client.application_uri = self.application_uri

            # Auto-accept server certificates
            self.client.security_checks = False

            # Connect to server
            logger.info(f"Connecting to {self.endpoint_url}...")
            self.client.connect()
            logger.info("Connected successfully!")

            # Set connected flag
            self.connected = True
            return True
        except Exception as e:
            logger.error(f"Error connecting to server: {e}")
            self.disconnect()
            return False

    def disconnect(self):
        """Disconnect from the OPC UA server and clean up resources"""
        if self.subscription:
            try:
                # Unsubscribe all handles
                for handle in self.handles:
                    self.subscription.unsubscribe(handle)
                self.handles = []

                # Delete the subscription
                self.subscription.delete()
                self.subscription = None
            except Exception as e:
                logger.warning(f"Error cleaning up subscription: {e}")

        if self.client:
            try:
                logger.info("Disconnecting from server...")
                self.client.disconnect()
                logger.info("Disconnected successfully")
            except Exception as e:
                logger.error(f"Error disconnecting: {e}")
            finally:
                self.client = None
                self.connected = False

    def subscribe_to_nodes(self):
        """Create subscriptions for all nodes to monitor"""
        if not self.connected or not self.client:
            logger.error("Cannot subscribe: not connected")
            return False

        try:
            # Create subscription
            handler = SubHandler(self)
            self.subscription = self.client.create_subscription(500, handler)
            logger.info("Created subscription with publishing interval of 500ms")

            # Subscribe to each node
            self.handles = []
            for node_info in self.nodes_to_monitor:
                try:
                    node_id = node_info["id"]
                    node = self.client.get_node(node_id)
                    handle = self.subscription.subscribe_data_change(node)
                    self.handles.append(handle)
                    logger.info(f"Subscribed to: {node_info['name']} ({node_id})")

                    # Read initial value
                    try:
                        value = node.get_value()
                        unit = node_info.get("unit", "")
                        logger.info(f"Initial value of {node_info['name']}: {value} {unit}")

                        # Store in latest values
                        self.latest_values[node_info["name"]] = {
                            "value": value,
                            "unit": unit,
                            "timestamp": datetime.datetime.now().isoformat()
                        }

                        # Notify callbacks
                        self._notify_callbacks(node_info["name"], value, unit)
                    except Exception as e:
                        logger.warning(f"Could not read initial value for {node_id}: {e}")
                except Exception as e:
                    logger.error(f"Failed to subscribe to {node_id}: {e}")

            return True
        except Exception as e:
            logger.error(f"Error subscribing to nodes: {e}")
            return False

    def _notify_callbacks(self, name, value, unit):
        """Notify all callbacks of a value change"""
        timestamp = datetime.datetime.now()
        for callback in self.value_callbacks:
            try:
                callback(name, value, unit, timestamp)
            except Exception as e:
                logger.error(f"Error in callback: {e}")


class SubHandler:
    """Subscription Handler for data change notifications"""

    def __init__(self, connector):
        self.connector = connector

    def datachange_notification(self, node, val, data):
        try:
            # Get node ID string
            node_id = node.nodeid.to_string()

            # Find matching node info
            node_info = next((n for n in self.connector.nodes_to_monitor if n["id"] == node_id), None)

            if node_info:
                name = node_info["name"]
                unit = node_info.get("unit", "")

                # Store in connector's latest values
                self.connector.latest_values[name] = {
                    "value": val,
                    "unit": unit,
                    "timestamp": datetime.datetime.now().isoformat()
                }

                # Format output for logging
                unit_str = f" {unit}" if unit else ""
                logger.info(f"{name}: {val}{unit_str}")

                # Notify callbacks
                self.connector._notify_callbacks(name, val, unit)
            else:
                logger.info(f"Data change for unknown node {node_id}: {val}")
        except Exception as e:
            logger.error(f"Error in datachange_notification: {e}")