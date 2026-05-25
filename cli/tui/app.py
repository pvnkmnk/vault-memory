"""Vault Memory TUI - Main application."""

import asyncio
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Markdown,
    ProgressBar,
    Static,
    Tabs,
    Tab,
)

DAEMON_URL = os.getenv("VAULT_MEMORY_URL", "http://127.0.0.1:5051")
API_KEY = os.getenv("VAULT_MEMORY_API_KEY", "")


class VaultMemoryTUI(App):
    """Main TUI application for vault-memory."""

    CSS = """
    Screen {
        background: $surface;
    }
    
    #main-container {
        padding: 1;
    }
    
    #header {
        text-align: center;
        text-style: bold;
        padding: 1;
    }
    
    #status-bar {
        dock: top;
        height: 3;
        background: $panel;
        padding: 0 1;
    }
    
    .search-box {
        margin: 1 0;
    }
    
    .results-container {
        height: 1fr;
        margin: 1 0;
    }
    
    .button-container {
        height: 3;
        dock: bottom;
    }
    
    DataTable {
        height: 1fr;
    }
    
    #health-panel {
        border: solid $primary;
        padding: 1;
        margin: 1 0;
    }
    
    #sync-panel {
        border: solid $secondary;
        padding: 1;
        margin: 1 0;
    }
    """

    TITLE = "Vault Memory TUI"
    SUB_TITLE = "Semantic Memory Layer for Obsidian"

    def __init__(self):
        super().__init__()
        self.daemon_url = DAEMON_URL
        self.api_key = API_KEY
        self.headers = {"x-api-key": self.api_key} if self.api_key else {}

    def compose(self) -> ComposeResult:
        """Compose the UI."""
        yield Header()
        yield Container(
            Static("Vault Memory TUI", id="header"),
            Tabs(
                Tab("Search", id="search-tab"),
                Tab("Health", id="health-tab"),
                Tab("Sync", id="sync-tab"),
                Tab("Graph", id="graph-tab"),
                Tab("Temporal", id="temporal-tab"),
            ),
            Container(id="screen-container"),
            id="main-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the app on mount."""
        container = self.query_one("#screen-container", Container)
        # Mount all screens once
        container.mount(
            Vertical(
                Label("Search your vault:", classes="search-box"),
                Input(placeholder="Enter search query...", id="search-input"),
                Button("Search", id="search-button", variant="primary"),
                DataTable(id="search-results"),
                classes="results-container",
                id="search-screen"
            ),
            Vertical(
                Label("Daemon Health Status", id="health-panel"),
                Static("Loading health status...", id="health-status"),
                Button("Refresh", id="refresh-health-button", variant="primary"),
                classes="results-container",
                id="health-screen"
            ),
            Vertical(
                Label("Sync", id="sync-panel"),
                Static("Click 'Full Sync' to sync your vault.", id="sync-status"),
                Button("Full Sync", id="full-sync-button", variant="primary"),
                ProgressBar(id="sync-progress"),
                classes="results-container",
                id="sync-screen"
            ),
            Vertical(
                Label("Graph Traversal", id="graph-panel"),
                Input(placeholder="Enter entity name...", id="graph-entity-input"),
                Input(placeholder="Relationship type (optional)...", id="graph-rel-input"),
                Button("Traverse", id="graph-traverse-button", variant="primary"),
                DataTable(id="graph-results"),
                classes="results-container",
                id="graph-screen"
            ),
            Vertical(
                Label("Temporal Query", id="temporal-panel"),
                Input(placeholder="Enter entity name...", id="temporal-entity-input"),
                Input(placeholder="Start date (YYYY-MM-DD)...", id="temporal-start-input", value="2025-01-01"),
                Input(placeholder="End date (YYYY-MM-DD)...", id="temporal-end-input", value="2026-12-31"),
                Button("Query", id="temporal-query-button", variant="primary"),
                DataTable(id="temporal-results"),
                classes="results-container",
                id="temporal-screen"
            )
        )
        # Show only search screen initially
        self._show_screen("search-screen")
        # Load initial health status after a short delay
        self.set_timer(0.1, self._load_health_status)

    def _show_screen(self, screen_id: str) -> None:
        """Show only the specified screen, hide others."""
        container = self.query_one("#screen-container", Container)
        for child in container.children:
            child.display = (child.id == screen_id)

    def on_input_blurred(self, event: Input.Blurred) -> None:
        """Handle Input.Blurred message - state persists naturally since screens aren't removed."""
        pass

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        """Handle tab activation."""
        tab_id = event.tab.id
        screen_map = {
            "search-tab": "search-screen",
            "health-tab": "health-screen",
            "sync-tab": "sync-screen",
            "graph-tab": "graph-screen",
            "temporal-tab": "temporal-screen"
        }
        if tab_id in screen_map:
            self._show_screen(screen_map[tab_id])
            if tab_id == "health-tab":
                self.set_timer(0.1, self._load_health_status)
    
    async def _load_health_status(self) -> None:
        """Load and display health status."""
        try:
            async with httpx.AsyncClient() as client:
                liveness_resp = await client.get(f"{self.daemon_url}/health", headers=self.headers, timeout=5.0)
                readiness_resp = await client.get(f"{self.daemon_url}/ready", headers=self.headers, timeout=5.0)
                
                if liveness_resp.status_code == 200 and readiness_resp.status_code == 200:
                    liveness = liveness_resp.json()
                    readiness = readiness_resp.json()
                    
                    # Get last sync time from sync_state table via direct query
                    last_sync = "never"
                    try:
                        import psycopg2
                        from daemon.config import Settings
                        settings = Settings()
                        with psycopg2.connect(settings.pg_connection_string) as pg_conn:
                            with pg_conn.cursor() as cursor:
                                cursor.execute("SELECT MAX(last_synced_at) FROM sync_state WHERE is_deleted = FALSE")
                                result = cursor.fetchone()
                                if result and result[0]:
                                    last_sync = result[0].strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass  # Fall back to "never"
                    uptime_seconds = readiness.get('uptime_seconds', 0)
                    if uptime_seconds == 0:
                        uptime_str = "unknown"
                    elif uptime_seconds < 60:
                        uptime_str = f"{uptime_seconds:.0f}s"
                    elif uptime_seconds < 3600:
                        uptime_str = f"{uptime_seconds/60:.1f}m"
                    else:
                        uptime_str = f"{uptime_seconds/3600:.1f}h"
                    
                    status_text = f"""
Status: {readiness.get('status', 'unknown')}
Uptime: {uptime_str}
Last Sync: {last_sync}
Dependencies: {len(liveness.get('dependencies', {}))} services
                    """.strip()
                    
                    self.query_one("#health-status", Static).update(status_text)
                else:
                    self.query_one("#health-status", Static).update("Failed to fetch health status")
        except Exception as e:
            self.query_one("#health-status", Static).update(f"Error: {str(e)}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id

        if button_id == "search-button":
            asyncio.create_task(self._perform_search())
        elif button_id == "refresh-health-button":
            asyncio.create_task(self._load_health_status())
        elif button_id == "check-drift-button":
            self._check_drift()
        elif button_id == "full-sync-button":
            self._trigger_full_sync()
        elif button_id == "graph-traverse-button":
            self._perform_graph_traversal()
        elif button_id == "temporal-query-button":
            self._perform_temporal_query()

    async def _perform_search(self) -> None:
        """Perform search query."""
        query = self.query_one("#search-input", Input).value
        if not query:
            return

        try:
            results_table = self.query_one("#search-results", DataTable)
            results_table.clear(columns=True)
            results_table.add_column("Path", key="path")
            results_table.add_column("Score", key="score")
            results_table.add_column("Snippet", key="snippet")

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.daemon_url}/search",
                    json={"query": query, "top_k": 10},
                    headers=self.headers,
                    timeout=30.0
                )

            if response.status_code == 200:
                data = response.json()
                for result in data.get("results", []):
                    results_table.add_row(
                        result.get("path", ""),
                        f"{result.get('score', 0):.3f}",
                        result.get("snippet", "")[:100] + "..." if len(result.get("snippet", "")) > 100 else result.get("snippet", "")
                    )
            else:
                results_table.add_row("Error", "", f"HTTP {response.status_code}")
        except Exception as e:
            results_table = self.query_one("#search-results", DataTable)
            results_table.clear(columns=True)
            results_table.add_column("Error")
            results_table.add_row(str(e))

    def _check_drift(self) -> None:
        """Check for sync drift."""
        status_label = self.query_one("#sync-status", Static)
        status_label.update("Checking drift...")
        
        try:
            import httpx
            response = httpx.post(
                f"{self.daemon_url}/sync/delta",
                json={"since": "2025-01-01T00:00:00Z"},
                headers=self.headers,
                timeout=30.0
            )
            
            if response.status_code == 200:
                data = response.json()
                changed = len(data.get("changed", []))
                deleted = len(data.get("deleted", []))
                status_label.update(f"Drift check complete: {changed} changed, {deleted} deleted")
            else:
                status_label.update(f"Error: HTTP {response.status_code}")
        except Exception as e:
            status_label.update(f"Error: {str(e)}")

    def _trigger_full_sync(self) -> None:
        """Trigger full sync (note: this is a long-running operation)."""
        status_label = self.query_one("#sync-status", Static)
        progress_bar = self.query_one("#sync-progress", ProgressBar)
        
        status_label.update("Starting full sync...")
        progress_bar.update(total=100, progress=0)
        
        # Run sync command as subprocess
        import subprocess
        import threading
        import sys
        
        def run_sync():
            try:
                vault_path = os.getenv("VAULT_PATH", "")
                embedding_model = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
                
                # Try to read vault path from config file if not set
                if not vault_path:
                    config_path = os.path.expanduser("~/.vault-memory.json")
                    if os.path.exists(config_path):
                        import json
                        with open(config_path, 'r') as f:
                            config = json.load(f)
                            vault_path = config.get("vault_path", "")
                
                if not vault_path:
                    self.call_from_thread(status_label.update, "Error: VAULT_PATH not set. Set it in ~/.vault-memory.json or environment variable.")
                    return
                
                # Find vault-memory executable
                vault_memory_exe = None
                if sys.platform == "win32":
                    vault_memory_exe = shutil.which("vault-memory.exe") or shutil.which("vault-memory")
                else:
                    vault_memory_exe = shutil.which("vault-memory")
                
                if not vault_memory_exe:
                    self.call_from_thread(status_label.update, "Error: vault-memory executable not found")
                    return
                
                # Pass environment variables to subprocess
                env = os.environ.copy()
                env["VAULT_PATH"] = vault_path
                env["EMBEDDING_MODEL"] = embedding_model
                
                cmd = [
                    vault_memory_exe,
                    "sync",
                    "--full",
                    "--vault", vault_path,
                    "--embedding-model", embedding_model,
                    "--plain-output"
                ]
                
                self.call_from_thread(status_label.update, f"Starting: {' '.join(cmd)}")

                # Set debug log path to temp directory with timestamp
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                debug_log_path = Path(tempfile.gettempdir()) / f"tui_sync_debug_{timestamp}.log"
                # Store the command for later writing
                command_line = f"Starting sync: {' '.join(cmd)}\n"

                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=os.getcwd(),
                    env=env
                )
                
                # Update progress as sync runs
                output_lines = []
                debug_log = []
                for line in process.stdout:
                    line_stripped = line.strip()
                    output_lines.append(line_stripped)
                    debug_log.append(line_stripped)
                    self.call_from_thread(status_label.update, f"Syncing... {line_stripped[:80]}")
                    # Parse PROGRESS: XX% format
                    if "PROGRESS:" in line and "%" in line:
                        try:
                            # Parse "PROGRESS: 50% (10/20 files)"
                            progress_part = line.split("PROGRESS:")[1].split("%")[0].strip()
                            progress = int(progress_part)
                            self.call_from_thread(progress_bar.update, progress=progress)
                        except (ValueError, IndexError):
                            pass
                
                process.wait()

                # Write debug log to file
                if debug_log_path:
                    try:
                        with open(debug_log_path, "w") as f:
                            f.write(command_line)
                            f.write("\n".join(debug_log))
                        self.call_from_thread(status_label.update, f"Debug log written to {debug_log_path}")
                    except Exception:
                        pass

                if not output_lines:
                    self.call_from_thread(status_label.update, "No output from sync command")

                if process.returncode == 0:
                    self.call_from_thread(status_label.update, "Full sync completed successfully!")
                    self.call_from_thread(progress_bar.update, progress=100)
                else:
                    self.call_from_thread(status_label.update, f"Sync failed with exit code {process.returncode}")
            except Exception as e:
                self.call_from_thread(status_label.update, f"Error: {str(e)}")
        
        # Run sync in background thread
        thread = threading.Thread(target=run_sync)
        thread.daemon = True
        thread.start()

    def _perform_graph_traversal(self) -> None:
        """Perform graph traversal."""
        entity = self.query_one("#graph-entity-input", Input).value
        rel = self.query_one("#graph-rel-input", Input).value
        
        if not entity:
            return
        
        try:
            results_table = self.query_one("#graph-results", DataTable)
            results_table.clear()
            results_table.add_column("Source", key="source")
            results_table.add_column("Target", key="target")
            results_table.add_column("Type", key="type")
            
            import httpx
            params = {"entity": entity}
            if rel:
                params["relationship"] = rel
            
            response = httpx.get(
                f"{self.daemon_url}/graph",
                params=params,
                headers=self.headers,
                timeout=10.0
            )
            
            if response.status_code == 200:
                data = response.json()
                for edge in data.get("edges", []):
                    results_table.add_row(
                        edge.get("source", ""),
                        edge.get("target", ""),
                        edge.get("relationship_type", "")
                    )
            else:
                results_table.add_row("Error", "", f"HTTP {response.status_code}")
        except Exception as e:
            results_table = self.query_one("#graph-results", DataTable)
            results_table.clear()
            results_table.add_column("Error")
            results_table.add_row(str(e))

    def _perform_temporal_query(self) -> None:
        """Perform temporal query."""
        entity = self.query_one("#temporal-entity-input", Input).value
        start = self.query_one("#temporal-start-input", Input).value
        end = self.query_one("#temporal-end-input", Input).value
        
        if not entity:
            return
        
        try:
            results_table = self.query_one("#temporal-results", DataTable)
            results_table.clear()
            results_table.add_column("Entity", key="entity")
            results_table.add_column("Valid From", key="valid_from")
            results_table.add_column("Valid To", key="valid_to")
            results_table.add_column("Properties", key="properties")
            
            import httpx
            response = httpx.get(
                f"{self.daemon_url}/temporal",
                params={"entity": entity, "start": start, "end": end},
                headers=self.headers,
                timeout=10.0
            )
            
            if response.status_code == 200:
                data = response.json()
                for item in data.get("entities", []):
                    results_table.add_row(
                        item.get("entity_name", ""),
                        item.get("valid_from", ""),
                        item.get("valid_to", "") or "present",
                        str(item.get("properties", {}))[:50] + "..." if len(str(item.get("properties", {}))) > 50 else str(item.get("properties", {}))
                    )
            else:
                results_table.add_row("Error", "", "", f"HTTP {response.status_code}")
        except Exception as e:
            results_table = self.query_one("#temporal-results", DataTable)
            results_table.clear()
            results_table.add_column("Error")
            results_table.add_row(str(e))


def run_tui():
    """Run the TUI application."""
    app = VaultMemoryTUI()
    app.run()
