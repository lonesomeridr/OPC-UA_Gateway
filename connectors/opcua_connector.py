# Innhold fra eksisterende opcua_connector.py
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

def safe_float(value):
    try:
        return round(float(value), 2)
    except (ValueError, TypeError):
        return None  # eller return 0.0 hvis foretrukket

# Konfigurer logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)


class OpcUaConnector:
    def __init__(self, config_file='config.ini'):
        self.config = configparser.ConfigParser()
        self.config.read(config_file)

        self.endpoint_url = self.config.get('OPCUA', 'server_url')
        self.application_uri = self.config.get('OPCUA', 'application_uri')
        self.security_policy = self.config.get('OPCUA', 'security_policy')
        self.security_mode = self.config.get('OPCUA', 'security_mode')

        self.nodes_to_monitor = []
        monitoring_section = self.config['MONITORING']

        i = 1
        while f'node{i}_id' in monitoring_section:
            node_id = monitoring_section[f'node{i}_id']
            node_name = monitoring_section[f'node{i}_name']
            node_unit = monitoring_section.get(f'node{i}_unit', '')
            self.nodes_to_monitor.append({'id': node_id, 'name': node_name, 'unit': node_unit})
            i += 1

        self.client = None
        self.subscription = None
        self.handles = []
        self.connected = False
        self.latest_values = {}
        self.value_callbacks = []

    def add_value_callback(self, callback):
        self.value_callbacks.append(callback)

    def generate_certificates(self):
        try:
            cert_dir = os.path.join(os.getcwd(), "certificates")
            if not os.path.exists(cert_dir):
                os.makedirs(cert_dir)

            cert_path = os.path.join(cert_dir, "certificate.der")
            private_key_path = os.path.join(cert_dir, "private_key.pem")

            if os.path.exists(cert_path) and os.path.exists(private_key_path):
                logger.info(f"Using existing certificates from {cert_dir}")
                return cert_path, private_key_path

            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            with open(private_key_path, "wb") as f:
                f.write(private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption()
                ))

            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, u"OPC UA Client"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"My Organization"),
                x509.NameAttribute(NameOID.COUNTRY_NAME, u"NO")
            ])

            try:
                now = datetime.datetime.now(datetime.UTC)
            except AttributeError:
                now = datetime.datetime.utcnow()

            san = x509.SubjectAlternativeName([
                x509.DNSName(u"localhost"),
                x509.UniformResourceIdentifier(self.application_uri)
            ])

            cert = x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)\
                .public_key(private_key.public_key()).serial_number(x509.random_serial_number())\
                .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=365))\
                .add_extension(san, critical=False).sign(private_key, hashes.SHA256())

            with open(cert_path, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.DER))

            return cert_path, private_key_path
        except Exception as e:
            logger.error(f"Error generating certificates: {e}")
            raise

    def connect(self):
        try:
            cert_path, private_key_path = self.generate_certificates()

            self.client = Client(self.endpoint_url)
            security_string = f"{self.security_policy},{self.security_mode},{cert_path},{private_key_path}"
            logger.info(f"Setting security with string: {security_string}")
            self.client.set_security_string(security_string)

            self.client.application_uri = self.application_uri
            self.client.security_checks = False
            logger.info(f"Connecting to {self.endpoint_url}...")
            self.client.connect()
            self.connected = True
            return True
        except Exception as e:
            logger.error(f"Error connecting to server: {e}")
            self.disconnect()
            return False

    def disconnect(self):
        if self.subscription:
            try:
                for handle in self.handles:
                    self.subscription.unsubscribe(handle)
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
        if not self.connected or not self.client:
            logger.error("Cannot subscribe: not connected")
            return False

        try:
            handler = SubHandler(self)
            self.subscription = self.client.create_subscription(500, handler)
            logger.info("Created subscription with publishing interval of 500ms")

            self.handles = []
            for node_info in self.nodes_to_monitor:
                try:
                    node_id = node_info["id"]
                    node = self.client.get_node(node_id)
                    handle = self.subscription.subscribe_data_change(node)
                    self.handles.append(handle)
                    logger.info(f"Subscribed to: {node_info['name']} ({node_id})")

                    try:
                        value = safe_float(node.get_value())
                        unit = node_info.get("unit", "")
                        logger.info(f"Initial value of {node_info['name']}: {value} {unit}")
                        self.latest_values[node_info["name"]] = {
                            "value": value,
                            "unit": unit,
                            "timestamp": datetime.datetime.now().isoformat()
                        }
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
        timestamp = datetime.datetime.now()
        for callback in self.value_callbacks:
            try:
                callback(name, value, unit, timestamp)
            except Exception as e:
                logger.error(f"Error in callback: {e}")


class SubHandler:
    def __init__(self, connector):
        self.connector = connector

    def datachange_notification(self, node, val, data):
        try:
            node_id = node.nodeid.to_string()
            node_info = next((n for n in self.connector.nodes_to_monitor if n["id"] == node_id), None)

            if node_info:
                name = node_info["name"]
                unit = node_info.get("unit", "")
                value = safe_float(val)

                self.connector.latest_values[name] = {
                    "value": value,
                    "unit": unit,
                    "timestamp": datetime.datetime.now().isoformat()
                }

                unit_str = f" {unit}" if unit else ""
                logger.info(f"{name}: {value}{unit_str}")
                self.connector._notify_callbacks(name, value, unit)
            else:
                logger.info(f"Data change for unknown node {node_id}: {val}")
        except Exception as e:
            logger.error(f"Error in datachange_notification: {e}")