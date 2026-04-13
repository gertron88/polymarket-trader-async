"""
Main entry point for the Polymarket trading bot.

This module provides the orchestration layer that:
- Loads configuration from YAML
- Initializes all components
- Starts WebSocket feeds
- Runs the trading engine
- Handles graceful shutdown
- Provides error recovery
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

import yaml
import structlog

import sys
sys.path.insert(0, str(Path(__file__).parent))
from trading.engine import TradingEngine

# Configure structured logging
def setup_logging(config: dict) -> None:
    """Configure structured logging with appropriate handlers."""
    log_level = config.get('logging', {}).get('level', 'INFO')
    log_format = config.get('logging', {}).get('format', 'json')
    
    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer() if log_format == 'json' else structlog.dev.ConsoleRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    
    # Set root logger level
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(message)s',
        stream=sys.stdout,
    )


# Global logger
logger = structlog.get_logger(__name__)


async def load_config(config_path: str = 'config/settings.yaml') -> dict:
    """Load configuration from YAML file.
    
    Args:
        config_path: Path to the configuration file
        
    Returns:
        Configuration dictionary
        
    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If config file is invalid
    """
    config_file = Path(config_path)
    
    if not config_file.exists():
        # Try relative to workspace
        workspace = Path(__file__).parent.parent
        config_file = workspace / config_path
    
    logger.info("Loading configuration", config_path=str(config_file))
    
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)
    
    logger.info("Configuration loaded successfully")
    return config


async def shutdown(engine: TradingEngine, signal_name: str = None) -> None:
    """Perform graceful shutdown of the trading engine.
    
    Args:
        engine: The trading engine to shut down
        signal_name: Optional signal that triggered shutdown
    """
    if signal_name:
        logger.info("Shutdown signal received", signal=signal_name)
    else:
        logger.info("Initiating graceful shutdown...")
    
    try:
        await engine.close()
        logger.info("Shutdown completed successfully")
    except Exception as e:
        logger.error("Error during shutdown", error=str(e), exc_info=True)
        raise


def handle_signal(engine: TradingEngine, sig: signal.Signals) -> None:
    """Handle OS signals for graceful shutdown.
    
    Args:
        engine: The trading engine to shut down
        sig: The signal that was received
    """
    signal_name = sig.name if hasattr(sig, 'name') else str(sig)
    logger.warning("Received signal", signal=signal_name)
    
    # Create shutdown task
    asyncio.create_task(shutdown(engine, signal_name))


async def main() -> None:
    """Main entry point for the trading bot.
    
    This function:
    1. Loads configuration from YAML
    2. Sets up structured logging
    3. Initializes the trading engine
    4. Sets up signal handlers for graceful shutdown
    5. Runs the trading engine
    6. Handles errors and performs cleanup
    """
    config = None
    engine = None
    
    try:
        # Load configuration
        config = await load_config()
        
        # Setup logging
        setup_logging(config)
        
        logger.info("Starting Polymarket Trading Bot")
        logger.info("Configuration loaded", 
                   environment=config.get('environment', 'unknown'),
                   log_level=config.get('logging', {}).get('level', 'INFO'))
        
        # Initialize trading engine
        engine = TradingEngine(config)
        await engine.initialize()
        
        logger.info("Trading engine initialized successfully")
        
        # Setup signal handlers for graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: handle_signal(engine, s))
        
        logger.info("Signal handlers registered", signals=['SIGINT', 'SIGTERM'])
        logger.info("Starting trading engine...")
        
        # Run the trading engine
        try:
            await engine.run()
        except asyncio.CancelledError:
            logger.info("Trading engine cancelled")
            raise
        except Exception as e:
            logger.error(f"Fatal error in trading engine: {e}", exc_info=True)
            raise
            
    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {e}")
        sys.exit(1)
    except yaml.YAMLError as e:
        logger.error(f"Invalid configuration file: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Ensure cleanup happens even on error
        if engine:
            try:
                await shutdown(engine)
            except Exception as e:
                logger.error(f"Error during final cleanup: {e}")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete. Goodbye!")
        sys.exit(0)
