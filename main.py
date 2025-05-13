#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OPC-UA to Unity Gateway
Main application entry point
"""
import os
import time
import signal
import sys
import configparser
import logging
import argparse
from connectors.opcua_connector import OpcUaConnector
from connectors.unity_connector import UnityConnector
from connectors.db_connector import DbConnector
from utils.logging_utils import setup_logger, get_log_level

# Global objects for signal handlers
opcua_connector = None
unity_connector = None
db_connector = None


def signal_handler(sig, frame):
    """Handle termination signals gracefully"""
    logger.info("Received termination signal. Shutting down...")

    # Stop the Unity connector first
    if unity_connector and unity_connector.running:
        unity_connector.stop()

    # Then disconnect from OPC UA
    if opcua_connector and opcua_connector.connected:
        opcua_connector.disconnect()

    # Close database connection
    if db_connector and db_connector.connected:
        db_connector.disconnect()

    sys.exit(0)


def db_update_callback(name, value, unit, timestamp):
    """Callback for updating database with OPC UA values"""
    if db_connector and db_connector.connected:
        db_connector.update_value(name, value, unit, timestamp)


def main():
    """Main function to start the gateway"""
    global opcua_connector, unity_connector, db_connector

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='OPC-UA to Unity Gateway')
    parser.add_argument('--config', default='config.ini', help='Path to config file')
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                        help='Logging level')
    args = parser.parse_args()

    # Load configuration
    config = configparser.ConfigParser()
    config_file = args.config

    if not os.path.exists(config_file):
        print(f"Configuration file not found: {config_file}")
        return 1

    config.read(config_file)

    # Get log level from config or command line
    log_level_str = config.get('LOGGING', 'level', fallback=args.log_level)
    log_level = get_log_level(log_level_str)

    # Set up logging
    global logger
    logger = setup_logger('opcua_gateway', log_level,
                          log_to_file=config.getboolean('LOGGING', 'log_to_file', fallback=False),
                          log_dir=config.get('LOGGING', 'log_dir', fallback='logs'))

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Initialize OPC-UA connector
    logger.info("Initializing OPC-UA connector...")
    opcua_connector = OpcUaConnector(config_file)

    # Connect to OPC-UA server
    if not opcua_connector.connect():
        logger.error("Failed to connect to OPC-UA server")
        return 1

    # Subscribe to nodes
    if not opcua_connector.subscribe_to_nodes():
        logger.error("Failed to subscribe to OPC-UA nodes")
        opcua_connector.disconnect()
        return 1

    # Initialize database connector if enabled
    if config.getboolean('DATABASE', 'enabled', fallback=False):
        logger.info("Initializing database connector...")
        db_connector = DbConnector(config_file)
        if db_connector.connect():
            # Register database callback with OPC-UA connector
            opcua_connector.add_value_callback(db_update_callback)
            logger.info("Database logging enabled")

            # Log startup event
            db_connector.log_event("system", "OPC-UA Gateway service started", "info")
        else:
            logger.warning("Database connection failed, continuing without database logging")

    # Initialize and start Unity connector
    logger.info("Starting Unity HTTP connector...")
    unity_connector = UnityConnector(opcua_connector, config)

    # Connect Unity connector to database if available
    if db_connector and db_connector.connected:
        unity_connector.set_db_connector(db_connector)

    # Start the Unity connector
    if not unity_connector.start():
        logger.error("Failed to start Unity connector")
        opcua_connector.disconnect()
        if db_connector and db_connector.connected:
            db_connector.disconnect()
        return 1

    logger.info("OPC-UA Gateway started successfully")

    # Keep main thread running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        # Clean shutdown
        if unity_connector:
            unity_connector.stop()
        if opcua_connector:
            opcua_connector.disconnect()
        if db_connector and db_connector.connected:
            # Log shutdown event
            db_connector.log_event("system", "OPC-UA Gateway service stopped", "info")
            db_connector.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())