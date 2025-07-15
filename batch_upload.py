import os
import pandas as pd
from flask import jsonify, request, current_app
import logging
from pathlib import Path
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

# Импортируем функции из utils
try:
    from utils import resize_cover_image
except ImportError:
    logger.error("Could not import resize_cover_image function from utils")
    # Определяем заглушку, чтобы не вызывать ошибок при импорте
    def resize_cover_image(image_path, min_size=1400, max_size=3000, background_color=(0, 0, 0)):
        return image_path

def init_batch_upload_routes(app):
    """Initialize batch upload routes"""
    
    @app.route('/api/batch_upload/metadata', methods=['GET'])
    def get_metadata():
        """Get metadata from Excel file for batch upload
        
        Query parameters:
        language - Language code (e.g., 'english', 'russian')
        """
        language = request.args.get('language', 'english').lower()
        
        try:
            # Look for an Excel file with the language name - check both lowercase and capitalized first letter
            possible_filenames = [f"{language}.xlsx", f"{language.capitalize()}.xlsx"]
            excel_path = None
            
            # Directories to check
            search_dirs = [
                app.root_path,
                os.path.join(app.root_path, 'data'),
                os.path.join(app.root_path, 'spreadsheets'),
                os.path.join(app.root_path, 'metadata')
            ]
            
            # Детальное логирование поиска файлов
            logger.info(f"Searching for Excel file for language: {language}")
            logger.info(f"Possible filenames: {possible_filenames}")
            logger.info(f"Search directories: {search_dirs}")
            
            all_checked_paths = []
            
            try:
                # Check all combinations of dirs and filenames
                for directory in search_dirs:
                    for filename in possible_filenames:
                        path = os.path.join(directory, filename)
                        all_checked_paths.append(path)
                        logger.info(f"Checking path: {path}, exists: {os.path.exists(path)}")
                        if os.path.exists(path):
                            excel_path = path
                            logger.info(f"Found Excel file at: {excel_path}")
                            break
                    if excel_path:
                        break
            except Exception as e:
                logger.warning(f"Error checking alternate paths: {e}")
            
            if not excel_path or not os.path.exists(excel_path):
                logger.error(f"Excel file not found for language: {language}")
                logger.error(f"Checked paths: {all_checked_paths}")
                return jsonify({
                    'error': f'Excel file not found for language: {language}',
                    'checked_paths': all_checked_paths
                }), 404
                
            logger.info(f"Using Excel file: {excel_path}")
            
            if not os.path.exists(excel_path):
                return jsonify({
                    'error': f'Excel file not found for language: {language}',
                    'path': excel_path
                }), 404
            
            # Read Excel file with enhanced logging
            logger.info(f"Чтение Excel-файла: {excel_path}")
            try:
                df = pd.read_excel(excel_path)
                logger.info(f"Успешно прочитан Excel-файл, найдено строк: {len(df)}")
                logger.info(f"Колонки в Excel: {list(df.columns)}")
            except Exception as e:
                logger.error(f"Ошибка при чтении Excel-файла: {str(e)}")
                return jsonify({'error': f'Ошибка чтения Excel: {str(e)}'}), 500
            
            # Convert to JSON-friendly format
            metadata = []
            for idx, row in df.iterrows():
                try:
                    # Лучше обработать возможные названия колонок
                    number_col = next((c for c in ['Number', 'Episode', 'Номер', '№', 'N'] if c in df.columns), None)
                    title_col = next((c for c in ['Title', 'Название', 'Name'] if c in df.columns), None)
                    summary_col = next((c for c in ['Summary', 'Description', 'Описание'] if c in df.columns), None)
                    about_col = next((c for c in ['About', 'Full description', 'Полное описание'] if c in df.columns), None)
                    genre_col = next((c for c in ['Genre', 'Genres', 'Жанр', 'Жанры', 'Tags'] if c in df.columns), None)
                    
                    # Извлекаем данные с безопасным доступом
                    def safe_get(col_name):
                        if col_name and col_name in df.columns:
                            val = row[col_name]
                            if pd.isna(val):  # Проверка на NaN
                                return ''
                            return str(val)
                        return ''
                    
                    # Обработка номера эпизода с валидацией
                    try:
                        if number_col and number_col in df.columns:
                            number = int(row[number_col]) if not pd.isna(row[number_col]) else 0
                        else:
                            number = idx + 1  # Если нет колонки с номером, используем индекс строки + 1
                    except (ValueError, TypeError):
                        number = idx + 1
                    
                    # Создаем словарь с данными эпизода
                    episode_data = {
                        'number': number,
                        'title': safe_get(title_col) or f"Episode {number}",
                        'description': safe_get(summary_col),
                        'about': safe_get(about_col),
                        'genre': safe_get(genre_col)
                    }
                    
                    # Добавляем обработку жанров/тегов, если они разделены запятыми
                    if episode_data['genre']:
                        genres = [g.strip() for g in episode_data['genre'].split(',') if g.strip()]
                        episode_data['genres'] = genres
                    else:
                        episode_data['genres'] = []
                    
                    logger.info(f"Обработан эпизод {number}: {episode_data['title']}")
                    metadata.append(episode_data)
                except Exception as e:
                    logger.error(f"Ошибка при обработке строки {idx}: {str(e)}")
                    # Продолжаем обработку других строк при ошибке в одной строке
            
            # Sort by episode number
            metadata.sort(key=lambda x: x['number'])
            
            return jsonify(metadata), 200
        
        except Exception as e:
            logger.error(f"Error processing batch metadata: {str(e)}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/shows', methods=['GET'])
    def get_shows():
        """Get list of shows for the batch upload UI"""
        try:
            from show import get_shows_list
            shows = get_shows_list()
            
            # Convert to simplified format for UI
            show_list = []
            for show in shows:
                show_list.append({
                    'id': show['id'],
                    'title': show['title'],
                    'image': show.get('image', ''),
                    'genres': show.get('tags', [])
                })
            
            return jsonify(show_list), 200
            
        except Exception as e:
            logger.error(f"Error getting shows list: {str(e)}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/batch_upload/episodes', methods=['POST'])
    def create_batch_episodes():
        """Create multiple episodes from batch upload data"""
        try:
            data = request.json
            if not data or not data.get('episodes'):
                return jsonify({'error': 'No episode data provided'}), 400
            
            language = data.get('language', 'english')
            episodes = data.get('episodes', [])
            
            # Initialize counters
            created = 0
            failed = 0
            errors = []
            
            # Import needed functions
            try:
                from episode import create_episode
            except ImportError:
                return jsonify({'error': 'Episode module not found'}), 500
            
            for episode_data in episodes:
                try:
                    # Extract required fields
                    show_id = episode_data.get('showId')
                    if not show_id or show_id == 'new':
                        errors.append(f"Episode {episode_data.get('number')}: No valid show ID")
                        failed += 1
                        continue
                    
                    # Prepare episode data for create_episode function
                    new_episode = {
                        'show_id': show_id,
                        'title': episode_data.get('title', f"Episode {episode_data.get('number')}"),
                        'number': episode_data.get('number', 0),
                        'description': episode_data.get('summary', ''),
                        'about': episode_data.get('description', ''),
                        'tags': episode_data.get('genres', [])
                    }
                    
                    # Проверяем наличие файлов для обработки
                    audio_file = episode_data.get('audioFile', None)
                    cover_file = episode_data.get('coverFile', None)
                    
                    # Здесь мы бы обрабатывали загрузку аудиофайла, если бы он был передан
                    
                    # Если есть файл обложки - обрабатываем его с помощью resize_cover_image
                    if cover_file:
                        try:
                            # Создание каталога для эпизода и сохранение обложки
                            # Пока это заглушка, реальная обработка будет добавлена позже
                            
                            # В полной реализации здесь бы было:
                            # 1. Сохранение файла обложки в каталог эпизода
                            # 2. Вызов resize_cover_image для обработки изображения
                            # 3. Добавление пути к изображению в метаданные эпизода
                            
                            logger.info(f"Processing cover image for episode {new_episode['number']} using resize_cover_image")
                            # Добавляем информацию о наличии обложки
                            new_episode['has_cover'] = True
                            
                        except Exception as e:
                            logger.error(f"Error processing cover image for episode {new_episode['number']}: {str(e)}")
                            # Ошибка обработки обложки не должна прерывать создание эпизода
                    
                    # Create the episode
                    episode_id = create_episode(new_episode)
                    
                    if episode_id:
                        created += 1
                        logger.info(f"Created episode {episode_id} for show {show_id}")
                    else:
                        failed += 1
                        errors.append(f"Episode {new_episode['number']}: Failed to create episode")
                
                except Exception as e:
                    failed += 1
                    errors.append(f"Episode {episode_data.get('number', 'Unknown')}: {str(e)}")
                    logger.error(f"Error creating episode: {str(e)}")
            
            return jsonify({
                'created': created,
                'failed': failed,
                'errors': errors
            }), 200
            
        except Exception as e:
            logger.error(f"Error in batch episode creation: {str(e)}")
            return jsonify({'error': str(e)}), 500
