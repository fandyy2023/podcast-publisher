"""
Module for episode management functionality.
Provides functions for creating, updating, and deleting podcast episodes.
"""

import os
import json
import time
import logging
import shutil
from pathlib import Path

# Setup logger
logger = logging.getLogger(__name__)

def create_episode(episode_data):
    """
    Creates a new episode based on the provided data.
    
    Args:
        episode_data (dict): Episode data with following keys:
            - show_id (str): ID of the show this episode belongs to
            - title (str): Episode title
            - number (int): Episode number
            - description (str): Short description/summary
            - about (str): Full description/content
            - tags (list): List of genre tags
    
    Returns:
        str: ID of the created episode, or None if creation failed
    """
    try:
        # Get required fields
        show_id = episode_data.get('show_id')
        title = episode_data.get('title', '')
        number = episode_data.get('number', 0)
        
        # Determine base directory structure
        base_dir = Path(__file__).resolve().parent
        shows_dir = base_dir / "shows"
        show_dir = shows_dir / show_id
        episodes_dir = show_dir / "episodes"
        
        # Check if directories exist
        if not show_dir.exists():
            logger.error(f"Show directory not found: {show_dir}")
            return None
        
        # Create episodes directory if it doesn't exist
        episodes_dir.mkdir(exist_ok=True)
        
        # Create episode ID from title or use number-based ID
        episode_id = create_episode_id(title) if title else f"episode-{number}"
        
        # If episode with this ID already exists, make it unique
        if (episodes_dir / episode_id).exists():
            # Try adding episode number
            episode_id = f"{episode_id}-{number}"
            
            # If still exists, add timestamp
            if (episodes_dir / episode_id).exists():
                timestamp = int(time.time())
                episode_id = f"{episode_id}-{timestamp}"
        
        # Create episode directory
        episode_dir = episodes_dir / episode_id
        episode_dir.mkdir(exist_ok=True)
        
        # Create episode config
        episode_config = {
            "title": title,
            "summary": episode_data.get('description', ''),
            "description": episode_data.get('about', ''),
            "number": number,
            "genres": episode_data.get('tags', []),
            "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "audio": None,  # Will be set later when audio is added
            "image": None   # Will be set later when image is added
        }
        
        # Save episode config
        with open(episode_dir / "config.json", "w", encoding="utf-8") as f:
            json.dump(episode_config, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Created episode {episode_id} for show {show_id}")
        return episode_id
        
    except Exception as e:
        logger.error(f"Error creating episode: {str(e)}")
        return None

def create_episode_id(title):
    """Creates a URL-safe episode ID from the title"""
    # Convert to lowercase
    slug = title.lower()
    
    # Replace special characters with dash
    import re
    slug = re.sub(r'[^\w\s-]', '', slug)
    
    # Replace spaces with dashes
    slug = re.sub(r'[\s]+', '-', slug.strip())
    
    # Limit length
    return slug[:50]

def update_episode(show_id, episode_id, episode_data):
    """
    Updates an existing episode with new data
    
    Args:
        show_id (str): ID of the show this episode belongs to
        episode_id (str): ID of the episode to update
        episode_data (dict): Updated episode data
        
    Returns:
        bool: True if update was successful, False otherwise
    """
    try:
        # Determine episode directory
        base_dir = Path(__file__).resolve().parent
        episode_dir = base_dir / "shows" / show_id / "episodes" / episode_id
        
        # Check if episode exists
        if not episode_dir.exists():
            logger.error(f"Episode directory not found: {episode_dir}")
            return False
        
        # Load existing config
        with open(episode_dir / "config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        
        # Update fields
        if 'title' in episode_data:
            config['title'] = episode_data['title']
        
        if 'description' in episode_data:
            config['summary'] = episode_data['description']
            
        if 'about' in episode_data:
            config['description'] = episode_data['about']
            
        if 'number' in episode_data:
            config['number'] = episode_data['number']
            
        if 'tags' in episode_data:
            config['genres'] = episode_data['tags']
        
        # Save updated config
        with open(episode_dir / "config.json", "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
            
        logger.info(f"Updated episode {episode_id} for show {show_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating episode: {str(e)}")
        return False

def delete_episode(show_id, episode_id):
    """
    Deletes an episode
    
    Args:
        show_id (str): ID of the show this episode belongs to
        episode_id (str): ID of the episode to delete
        
    Returns:
        bool: True if deletion was successful, False otherwise
    """
    try:
        # Determine episode directory
        base_dir = Path(__file__).resolve().parent
        episode_dir = base_dir / "shows" / show_id / "episodes" / episode_id
        
        # Check if episode exists
        if not episode_dir.exists():
            logger.error(f"Episode directory not found: {episode_dir}")
            return False
        
        # Delete directory and all contents
        shutil.rmtree(episode_dir)
        
        logger.info(f"Deleted episode {episode_id} from show {show_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error deleting episode: {str(e)}")
        return False

def get_episode(show_id, episode_id):
    """
    Gets episode data
    
    Args:
        show_id (str): ID of the show this episode belongs to
        episode_id (str): ID of the episode to get
        
    Returns:
        dict: Episode data or None if not found
    """
    try:
        # Determine episode directory
        base_dir = Path(__file__).resolve().parent
        episode_dir = base_dir / "shows" / show_id / "episodes" / episode_id
        
        # Check if episode exists
        if not episode_dir.exists():
            logger.error(f"Episode directory not found: {episode_dir}")
            return None
        
        # Load config
        with open(episode_dir / "config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
            
        # Add episode ID to data
        config['id'] = episode_id
        
        return config
        
    except Exception as e:
        logger.error(f"Error getting episode: {str(e)}")
        return None
