"""
External Agent Loader for Open WebUI

This module handles automatic loading and registration of agents from external packages.
Agents are installed and registered at application startup based on environment variables.

Usage:
    Set environment variables:
    - EXTERNAL_AGENTS_REPO: Git URL or local path to agents repo
    - EXTERNAL_AGENTS_PACKAGE: Python package name (e.g., 'genai_utils.agents')
      Note: Agents are expected at {package}.pipes.{agent_name}
    - EXTERNAL_AGENTS_LIST: Comma-separated list of agent module names to load
    - EXTERNAL_AGENTS_AUTO_UPDATE: If 'true', git pull latest on startup (default: false)

Example .env:
    EXTERNAL_AGENTS_REPO=https://github.com/Gradient-DS/genai-utils.git
    EXTERNAL_AGENTS_PACKAGE=genai_utils.agents
    EXTERNAL_AGENTS_LIST=neo_nl_agent,neo_nl_assistant,neo_nl_multiagent
    EXTERNAL_AGENTS_AUTO_UPDATE=false
    # Agents will be imported from: agents.pipes.{agent_name}
"""

import os
import sys
import subprocess
import logging
import importlib
import tempfile
from pathlib import Path
from typing import Optional, List, Dict
import time

log = logging.getLogger(__name__)

# Import Open WebUI models for direct database registration
try:
    from open_webui.models.functions import Functions, FunctionForm, FunctionMeta
    from open_webui.utils.plugin import load_function_module_by_id, extract_frontmatter
    REGISTRATION_AVAILABLE = True
except ImportError:
    log.warning("Could not import Open WebUI models - agent registration will be skipped")
    REGISTRATION_AVAILABLE = False


def get_repo_install_path() -> Path:
    """Get the path where external repos are installed."""
    cache_dir = os.environ.get("CACHE_DIR", "./data/cache")
    return Path(cache_dir) / "external_agents"


def install_external_package(
    repo_url: str,
    auto_update: bool = False
) -> Optional[Path]:
    """
    Install external package from git repo or local path.
    
    Args:
        repo_url: Git URL or local filesystem path
        auto_update: If True, pull latest changes from git
        
    Returns:
        Path to installed package, or None if installation failed
    """
    install_path = get_repo_install_path()
    install_path.mkdir(parents=True, exist_ok=True)
    
    # Check if it's a local path:
    if os.path.exists(repo_url):
        log.info(f"Using local external agents repo: {repo_url}")
        try:
            # Install in editable mode with better error reporting
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", repo_url],
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0:
                log.error(f"Failed to install local package")
                log.error(f"Pip stdout: {result.stdout}")
                log.error(f"Pip stderr: {result.stderr}")
                return None
            log.info(f"Successfully installed local package from: {repo_url}")
            return Path(repo_url)
        except subprocess.TimeoutExpired:
            log.error(f"Installation timed out after 60 seconds")
            return None
        except Exception as e:
            log.error(f"Failed to install local package: {e}")
            return None
    
    # If git URL:
    repo_name = repo_url.rstrip('/').split('/')[-1].replace('.git', '')
    repo_path = install_path / repo_name
    
    try:
        if repo_path.exists():
            if auto_update:
                log.info(f"Updating external agents repo: {repo_url}")
                subprocess.check_call(
                    ["git", "-C", str(repo_path), "pull"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            else:
                log.info(f"Using existing external agents repo: {repo_path}")
        else:
            log.info(f"Cloning external agents repo: {repo_url}")
            subprocess.check_call(
                ["git", "clone", repo_url, str(repo_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        
        # Install in editable mode with better error reporting
        log.info(f"Installing external agents package in editable mode...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(repo_path)],
            capture_output=True,
            text=True,
            timeout=300
        )
        if result.returncode != 0:
            log.error(f"Failed to install package from git repo")
            log.error(f"Pip stdout: {result.stdout}")
            log.error(f"Pip stderr: {result.stderr}")
            return None
        
        log.info(f"Successfully installed package from: {repo_url}")
        return repo_path
        
    except subprocess.CalledProcessError as e:
        log.error(f"Failed to install external package from {repo_url}: {e}")
        return None
    except Exception as e:
        log.error(f"Unexpected error installing external package: {e}")
        return None


def create_wrapper_content(
    package_path: str,
    agent_module: str,
    agent_metadata: Optional[Dict] = None
) -> str:
    """
    Create wrapper function content that imports from external package.
    
    Args:
        package_path: Python package path (e.g., 'genai_utils.agents')
                      Agents are expected at {package_path}.pipes.{agent_module}
        agent_module: Module name (e.g., 'neo_nl_agent')
        agent_metadata: Optional metadata dict with title, description, etc.
        
    Returns:
        Python code string for the wrapper function
    """
    # Append the new path structure: agents.pipes
    full_import_path = f"{package_path}.pipes.{agent_module}"
    
    # Use metadata if provided, otherwise use defaults
    title = agent_metadata.get("title", agent_module.replace("_", " ").title()) if agent_metadata else agent_module.replace("_", " ").title()
    description = agent_metadata.get("description", f"External agent: {agent_module}") if agent_metadata else f"External agent: {agent_module}"
    version = agent_metadata.get("version", "1.0.0") if agent_metadata else "1.0.0"
    requirements = agent_metadata.get("requirements", "") if agent_metadata else ""
    
    wrapper_template = f'''"""
title: {title}
description: {description}
author: External Agent (auto-loaded)
version: {version}
requirements: {requirements}
"""

import importlib
import logging

log = logging.getLogger(__name__)

# Import the external agent
try:
    external_module = importlib.import_module("{full_import_path}")
    ExternalPipe = getattr(external_module, "Pipe")
    log.info(f"Successfully loaded external agent: {full_import_path}")
except (ImportError, AttributeError) as e:
    log.error(f"Failed to import agent from {full_import_path}: {{e}}")
    raise ImportError(f"Failed to import agent from {full_import_path}: {{e}}")

# Re-export as Pipe for Open WebUI
class Pipe(ExternalPipe):
    """
    Wrapper around external agent - delegates all functionality.
    This allows the agent to be updated in the external repo without
    changing Open WebUI code.
    """
    pass
'''
    return wrapper_template


def extract_agent_metadata(package_path: str, agent_module: str) -> Optional[Dict]:
    """
    Try to import agent and extract metadata from its docstring.
    
    Args:
        package_path: Python package path
        agent_module: Module name
        
    Returns:
        Dict with metadata, or None if extraction fails
    """
    try:
        # Append the new path structure: agents.pipes
        full_import_path = f"{package_path}.pipes.{agent_module}"
        module = importlib.import_module(full_import_path)
        
        # Try to get metadata from module docstring
        metadata = {}
        if hasattr(module, "__doc__") and module.__doc__:
            import re
            doc = module.__doc__
            
            # Parse frontmatter-style metadata
            for line in doc.split('\n'):
                line = line.strip()
                match = re.match(r'^([a-z_]+):\s*(.+)$', line, re.IGNORECASE)
                if match:
                    key, value = match.groups()
                    metadata[key.lower()] = value.strip()
        
        return metadata if metadata else None
        
    except Exception as e:
        log.debug(f"Could not extract metadata from {agent_module}: {e}")
        return None


def register_external_agent_direct(
    agent_id: str,
    wrapper_content: str,
    user_id: str = "system"
) -> bool:
    """
    Register an external agent wrapper directly in the database.
    
    Args:
        agent_id: Function ID
        wrapper_content: Python code for the wrapper
        user_id: User ID to associate with the function (default: "system")
        
    Returns:
        True if successful, False otherwise
    """
    if not REGISTRATION_AVAILABLE:
        log.error("Registration not available - required imports failed")
        return False
    
    try:
        # Extract metadata from wrapper content
        frontmatter = extract_frontmatter(wrapper_content)
        
        # Load and validate the function module
        function_module, function_type, _ = load_function_module_by_id(
            agent_id,
            content=wrapper_content
        )
        
        # Prepare function data
        function_name = frontmatter.get("title", agent_id.replace("_", " ").title())
        function_description = frontmatter.get("description", f"External agent: {agent_id}")
        
        form_data = FunctionForm(
            id=agent_id,
            name=function_name,
            content=wrapper_content,
            meta=FunctionMeta(
                description=function_description,
                manifest=frontmatter
            )
        )
        
        # Check if function already exists
        existing_function = Functions.get_function_by_id(agent_id)
        
        if existing_function:
            # Update existing function
            log.info(f"Updating existing agent: {agent_id}")
            updated = Functions.update_function_by_id(
                agent_id,
                {
                    **form_data.model_dump(exclude={"id"}),
                    "type": function_type,
                    "is_active": True
                }
            )
            if updated:
                log.info(f"  ✓ Agent updated: {function_name}")
                return True
            else:
                log.error(f"  ✗ Failed to update agent: {agent_id}")
                return False
        else:
            # Create new function
            log.info(f"Creating new agent: {agent_id}")
            result = Functions.insert_new_function(user_id, function_type, form_data)
            
            if result:
                # Activate the function
                Functions.update_function_by_id(agent_id, {"is_active": True})
                log.info(f"  ✓ Agent created and activated: {function_name}")
                return True
            else:
                log.error(f"  ✗ Failed to create agent: {agent_id}")
                return False
                
    except Exception as e:
        log.error(f"Failed to register agent {agent_id}: {e}", exc_info=True)
        return False


def load_external_agents_at_startup() -> Dict[str, bool]:
    """
    Load external agents at application startup based on environment variables.
    This is called from the FastAPI lifespan context when starting the backend.
    
    Returns:
        Dict mapping agent_id to success status
    """
    results = {}
    
    # Read configuration from environment
    repo_url = os.environ.get("EXTERNAL_AGENTS_REPO", "").strip()
    package_name = os.environ.get("EXTERNAL_AGENTS_PACKAGE", "").strip()
    agents_list = os.environ.get("EXTERNAL_AGENTS_LIST", "").strip()
    auto_update = os.environ.get("EXTERNAL_AGENTS_AUTO_UPDATE", "false").lower() == "true"
    
    if not repo_url or not package_name or not agents_list:
        log.info("External agents not configured (EXTERNAL_AGENTS_* env vars not set)")
        return results
    
    log.info("=" * 60)
    log.info("Loading External Agents")
    log.info("=" * 60)
    log.info(f"Repository: {repo_url}")
    log.info(f"Package: {package_name}")
    log.info(f"Agents: {agents_list}")
    log.info(f"Auto-update: {auto_update}")
    
    # Install the external package
    repo_path = install_external_package(repo_url, auto_update)
    if not repo_path:
        log.error("Failed to install external agents package")
        return results
    
    # Parse agent list
    agent_modules = [a.strip() for a in agents_list.split(",") if a.strip()]
    
    # Create and register wrappers for each agent
    log.info(f"Creating and registering {len(agent_modules)} agent(s)...")
    
    for agent_module in agent_modules:
        agent_id = agent_module.lower()
        log.info(f"  - Processing {agent_id}...")
        
        try:
            # Extract metadata from the actual agent module
            metadata = extract_agent_metadata(package_name, agent_module)
            
            # Create wrapper content
            wrapper_content = create_wrapper_content(
                package_name,
                agent_module,
                metadata
            )
            
            # Register directly in database
            if register_external_agent_direct(agent_id, wrapper_content):
                results[agent_id] = True
            else:
                results[agent_id] = False
                
        except Exception as e:
            log.error(f"    ✗ Failed to process agent {agent_id}: {e}", exc_info=True)
            results[agent_id] = False
    
    log.info("=" * 60)
    log.info(f"External agent registration complete: {sum(results.values())}/{len(results)} successful")
    if sum(results.values()) > 0:
        log.info("✓ Agents are now available in the model selector!")
    log.info("=" * 60)
    
    return results


