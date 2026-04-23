"""
Google Apps Script Deployment Stats Fetcher

Retrieves and displays deployment statistics (error rate, executions, users)
from Google Apps Script projects via the Google Apps Script Execution API.

Requires:
  - google-auth library (pip install google-auth google-auth-httplib2 google-auth-oauthlib)
  - OAuth2 credentials.json file in project root

Setup Instructions:
  1. Go to https://console.cloud.google.com
  2. Create OAuth2 credentials (Service Account or Desktop Application)
  3. Enable Google Apps Script API
  4. Download credentials.json and place in project root
  5. Deploy your Apps Script and copy the Deployment ID

Stats fetched:
  - Total Executions: Number of function calls executed
  - Error Rate: Percentage of failed executions
  - Active Users: Number of unique users
  - Success Rate: Percentage of successful executions
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Dict, Any

from requests_oauthlib import OAuth2

from constants import GAS_STATS_TIMEOUT, GAS_STATS_API_BASE, GAS_CREDENTIALS_FILE

log = logging.getLogger("GAS-Stats")

@dataclass
class DeploymentStats:
    """Deployment statistics data container."""
    total_executions: int = 0
    failed_executions: int = 0
    successful_executions: int = 0
    error_rate: float = 0.0
    success_rate: float = 0.0
    active_users: int = 0
    timestamp: str = ""
    deployment_id: str = ""

    @property
    def ok(self) -> bool:
        """Check if stats were retrieved successfully."""
        return self.timestamp != ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)

    def __str__(self) -> str:
        """Pretty string representation."""
        if not self.ok:
            return "Stats unavailable"
        return (
            f"Executions: {self.total_executions} | "
            f"Success Rate: {self.success_rate:.1f}% | "
            f"Error Rate: {self.error_rate:.1f}% | "
            f"Active Users: {self.active_users}"
        )


class GASStatsFetcher:
    """
    Fetches Google Apps Script deployment statistics via the Execution API.

    Requires proper OAuth2 authentication setup with google-auth library.

    Attributes:
        deployment_id: The Apps Script Deployment ID
        credentials_path: Path to OAuth2 credentials.json file
        timeout: Request timeout in seconds (default: 10)
    """

    def __init__(
        self,
        deployment_id: str,
        credentials_path: Optional[str] = None,
        timeout: int = GAS_STATS_TIMEOUT,
    ):
        """
        Initialize the stats fetcher.

        Args:
            deployment_id: Google Apps Script Deployment ID
            credentials_path: Path to OAuth2 credentials.json file
            timeout: Request timeout in seconds
        """


        self.deployment_id = deployment_id
        self.credentials_path = credentials_path or GAS_CREDENTIALS_FILE
        self.timeout = timeout
        self._credentials = None

    def _load_credentials(self):
        """
        Load and validate OAuth2 credentials.

        Returns:
            Google credentials object

        Raises:
            FileNotFoundError: If credentials file not found
            ValueError: If credentials are invalid
        """
        if not os.path.exists(self.credentials_path):
            raise FileNotFoundError(
                f"OAuth2 credentials file not found: {self.credentials_path}\n"
                f"Set up credentials as described in module docstring."
            )

        try:
            self._credentials = service_account.Credentials.from_service_account_file(
                self.credentials_path,
                scopes=["https://www.googleapis.com/auth/script.projects.readonly"],
            )
            log.debug(f"Loaded credentials from {self.credentials_path}")
            return self._credentials
        except Exception as e:
            raise ValueError(f"Invalid credentials file: {e}")

    def _get_apps_script_service(self):
        """
        Get authenticated Google Apps Script service client.

        Returns:
            Google Apps Script API service object

        Raises:
            FileNotFoundError: If credentials not available
            ValueError: If credentials are invalid
        """
        if not self._credentials:
            self._load_credentials()

        return build("script", "v1", credentials=self._credentials, cache_discovery=False)

    def fetch_stats(self) -> DeploymentStats:
        """
        Fetch deployment statistics from Google Apps Script API.

        Returns:
            DeploymentStats object with metrics

        Raises:
            FileNotFoundError: If credentials file not found
            Exception: If API call fails
        """

        if not self.deployment_id:
            log.error("Deployment ID not set")
            return DeploymentStats()

        try:
            log.debug(f"Fetching stats for deployment {self.deployment_id}")

            # Load credentials
            self._load_credentials()

            # Get service client
            service = self._get_apps_script_service()

            # Call the Metrics API
            result = service.projects().getMetrics(projectId=self.deployment_id).execute()

            stats = self._parse_metrics(result)
            log.info(f"Successfully fetched GAS deployment stats")
            return stats

        except FileNotFoundError as e:
            log.error(f"Credentials error: {e}")
            return DeploymentStats()
        except ValueError as e:
            log.error(f"Invalid credentials: {e}")
            return DeploymentStats()
        except Exception as e:
            log.error(f"API error fetching GAS stats: {e}")
            return DeploymentStats()

    def _parse_metrics(self, response: Dict[str, Any]) -> DeploymentStats:
        """
        Parse metrics response from Google API.

        Args:
            response: API response dictionary

        Returns:
            DeploymentStats object with parsed metrics
        """
        stats = DeploymentStats(deployment_id=self.deployment_id)
        stats.timestamp = datetime.now().isoformat()

        try:
            # Extract metrics from response
            metrics = response.get("metrics", {})

            if not metrics:
                log.warning("No metrics data in API response")
                return stats

            # Parse execution statistics
            stats.total_executions = int(metrics.get("totalExecutions", 0))
            stats.failed_executions = int(metrics.get("failedExecutions", 0))
            stats.successful_executions = stats.total_executions - stats.failed_executions

            # Calculate rates
            if stats.total_executions > 0:
                stats.error_rate = (stats.failed_executions / stats.total_executions) * 100
                stats.success_rate = (stats.successful_executions / stats.total_executions) * 100

            # Parse user statistics
            stats.active_users = int(metrics.get("activeUsers", 0))

            log.debug(f"Metrics parsed: {stats}")

        except (KeyError, TypeError, ValueError) as e:
            log.error(f"Error parsing metrics response: {e}")

        return stats

    async def fetch_stats_async(self) -> DeploymentStats:
        """
        Fetch deployment statistics asynchronously.

        Returns:
            DeploymentStats object
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.fetch_stats)

    def fetch_and_log_stats(self) -> DeploymentStats:
        """
        Fetch stats and log them in a formatted manner.

        Uses the project's logging system for professional output.

        Returns:
            DeploymentStats object
        """
        stats = self.fetch_stats()

        if stats.ok:
            # Log stats in human-readable format with tree structure
            log.info("Google Apps Script Deployment Stats")
            log.info(
                f"├─ Total Executions: {stats.total_executions:,} | "
                f"Successful: {stats.successful_executions:,} | "
                f"Failed: {stats.failed_executions:,}"
            )
            log.info(
                f"├─ Success Rate: {stats.success_rate:>6.2f}% | "
                f"Error Rate: {stats.error_rate:>6.2f}%"
            )
            log.info(f"├─ Active Users: {stats.active_users:,}")
            log.info(f"└─ Deployment ID: {stats.deployment_id} | Timestamp: {stats.timestamp}")

        return stats


def create_fetcher(
    deployment_id: str,
    credentials_path: Optional[str] = None,
) -> GASStatsFetcher:
    """
    Factory function to create a GASStatsFetcher instance.

    Args:
        deployment_id: Google Apps Script Deployment ID
        credentials_path: Optional path to credentials.json file

    Returns:
        Configured GASStatsFetcher instance

    Raises:
        ImportError: If google-auth not installed
    """
    return GASStatsFetcher(
        deployment_id=deployment_id,
        credentials_path=credentials_path,
    )


def log_deployment_stats(
    deployment_id: str,
    credentials_path: Optional[str] = None,
) -> None:
    """
    Convenience function to fetch and log stats in one call.

    Args:
        deployment_id: Google Apps Script Deployment ID
        credentials_path: Optional path to credentials.json file

    Raises:
        ImportError: If google-auth not installed
    """
    try:
        fetcher = create_fetcher(deployment_id, credentials_path)
        fetcher.fetch_and_log_stats()
    except ImportError as e:
        log.error(f"Cannot fetch stats: {e}")
