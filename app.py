from flask import Flask, render_template, request, jsonify, session, send_file, send_from_directory
from yt_dlp import YoutubeDL
import os
import json
from concurrent.futures import ThreadPoolExecutor
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import logging
from datetime import datetime
import webview
import threading
import sys
import time
import pystray
from PIL import Image, ImageDraw

app = Flask(__name__)
app.secret_key = '1234567890'  # Add a secret key for session management
executor = ThreadPoolExecutor(max_workers=10)  # Augmenté à 10 workers

# Define directories and files paths
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), 'downloads')
CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')
PLAYLIST_FILE = os.path.join(os.path.dirname(__file__), 'playlists.json')
THEME_FILE = os.path.join(os.path.dirname(__file__), 'theme.json')
STATS_FILE = os.path.join(os.path.dirname(__file__), 'stats.json')

# Create necessary directories
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# Constants
MAX_PLAYLIST_SIZE = 1000
ALLOWED_AUDIO_FORMATS = ['m4a', 'mp3', 'wav']
CACHE_TIMEOUT = 3600  # 1 hour
MAX_CACHE_SIZE = 100

# Configuration Spotify
SPOTIFY_CLIENT_ID = ''
SPOTIFY_CLIENT_SECRET = ''

# Initialisation de l'API Spotify
sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET
))

# Configuration optimisée de yt-dlp
ydl_opts = {
    'format': 'bestaudio[ext=m4a]/bestaudio',
    'no_warnings': True,
    'quiet': True,
    'extract_flat': True,
    'skip_download': True,
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'no_call_home': True,
    'socket_timeout': 15,  # Augmenté pour plus de stabilité
    'retries': 5,  # Plus de tentatives
    'max_sleep_interval': 5,  # Limite le temps d'attente entre les tentatives
    'http_chunk_size': 10485760,  # 10MB - Améliore la stabilité du streaming
    'cachedir': CACHE_DIR,
    'prefer_ffmpeg': True,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'm4a',
        'preferredquality': '192',
    }]
}

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(__file__), 'app.log')),
        logging.StreamHandler()
    ]
)

# Cache initialization
audio_cache = {}
last_cache_cleanup = time.time()

def cleanup_cache():
    """Nettoie le cache périodiquement"""
    global last_cache_cleanup, audio_cache
    current_time = time.time()
    
    if current_time - last_cache_cleanup > 300:  # Toutes les 5 minutes
        audio_cache = {k: v for k, v in audio_cache.items() 
                      if current_time - v.get('timestamp', 0) < CACHE_TIMEOUT}
        while len(audio_cache) > MAX_CACHE_SIZE:
            audio_cache.pop(next(iter(audio_cache)))
        last_cache_cleanup = current_time

def get_audio_url(video_id):
    cleanup_cache()
    try:
        if video_id in audio_cache:
            cached = audio_cache[video_id]
            if time.time() - cached['timestamp'] < CACHE_TIMEOUT:
                return cached['data']
        
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            if not info:
                raise Exception("Could not extract video info")
                
            data = {
                'url': info.get('url'),
                'title': info.get('title', 'Unknown Title')
            }
            audio_cache[video_id] = {
                'data': data,
                'timestamp': time.time()
            }
            return data
    except Exception as e:
        logging.error(f"Error getting audio URL for {video_id}: {str(e)}")
        return None

def preload_next_song(playlist_name, current_index):
    if playlist_name not in playlists:
        return
    
    playlist = playlists[playlist_name]
    if current_index + 1 < len(playlist):
        next_song_id = playlist[current_index + 1]['id']
        if next_song_id not in audio_cache:
            audio_info = get_audio_url(next_song_id)
            if audio_info:
                audio_cache[next_song_id] = audio_info

def load_playlists():
    if os.path.exists(PLAYLIST_FILE):
        with open(PLAYLIST_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_playlists(playlists):
    with open(PLAYLIST_FILE, 'w') as f:
        json.dump(playlists, f, indent=2)

def load_stats():
    default_stats = {
        'totalSongsPlayed': 0,
        'totalHoursPlayed': 0.0,
        'lastUpdated': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE, 'r', encoding='utf-8') as f:
                loaded_stats = json.load(f)
                return {**default_stats, **loaded_stats}
        else:
            with open(STATS_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_stats, f, indent=2)
            return default_stats
    except Exception as e:
        logging.error(f"Error loading stats: {str(e)}")
        return default_stats

def save_stats(new_stats):
    try:
        if not isinstance(new_stats, dict):
            raise ValueError("Invalid stats format")
        
        new_stats['lastUpdated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if 'totalHoursPlayed' in new_stats:
            new_stats['totalHoursPlayed'] = round(float(new_stats['totalHoursPlayed']), 2)
        
        with open(STATS_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_stats, f, indent=2)
        return True
    except Exception as e:
        logging.error(f"Error saving stats: {str(e)}")
        return False

playlists = load_playlists()
stats = load_stats()

@app.route('/')
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'index.html')

@app.route('/search', methods=['POST'])
def search():
    query = request.json.get('query')
    with YoutubeDL(ydl_opts) as ydl:
        try:
            result = ydl.extract_info(f"ytsearch5:{query}", download=False)
            songs = []
            for entry in result['entries']:
                if entry:
                    songs.append({
                        'title': entry['title'],
                        'id': entry['id']
                    })
            return jsonify({'success': True, 'songs': songs})
        except Exception as e:
            print(f"Search error: {str(e)}")
            return jsonify({'success': False, 'error': 'Search failed'})

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def is_valid_response(response):
    """Vérifie si une réponse est valide"""
    return (
        response 
        and isinstance(response, dict)
        and response.get('success') is not None
    )

@app.after_request
def after_request(response):
    try:
        if response.status_code == 500:
            logging.error(f"Server error: {response.get_data(as_text=True)}")
            return jsonify({
                'success': False,
                'error': 'Une erreur est survenue, veuillez réessayer'
            }), 500
        
        # Vérifier que la réponse est bien formatée
        if response.mimetype == 'application/json':
            data = response.get_json()
            if not is_valid_response(data):
                logging.warning(f"Invalid response format: {data}")
                return jsonify({
                    'success': False,
                    'error': 'Format de réponse invalide'
                })
    except Exception as e:
        logging.error(f"Error in after_request: {str(e)}")
    
    return response

@app.errorhandler(Exception)
def handle_error(error):
    error_type = type(error).__name__
    error_msg = str(error)
    
    # Handle common HTTP errors
    if hasattr(error, 'code'):
        if error.code == 404:
            return jsonify({
                'success': False,
                'error': 'Resource not found'
            }), 404
        elif error.code == 400:
            return jsonify({
                'success': False,
                'error': 'Bad request'
            }), 400
    
    # Handle Spotify API errors
    if isinstance(error, spotipy.SpotifyException):
        return jsonify({
            'success': False,
            'error': 'Spotify API error',
            'details': error_msg
        }), 400
        
    # Handle download errors
    if 'DownloadError' in error_type:
        return jsonify({
            'success': False,
            'error': 'Download error',
            'details': error_msg
        }), 400
        
    # Handle specific error cases
    if 'NotFound' in error_type:
        return jsonify({
            'success': False,
            'error': 'Resource not found'
        }), 404
    
    # Log unexpected errors
    logging.error(f"Unexpected error {error_type}: {error_msg}")
    
    # Return a generic error for unexpected cases
    return jsonify({
        'success': False,
        'error': 'An error occurred while processing your request',
        'type': error_type
    }), 500

@app.route('/play/<video_id>')
def play(video_id):
    try:
        if not video_id:
            return jsonify({'success': False, 'error': 'ID de vidéo manquant'}), 400

        start_time = time.time()
        info = get_audio_url(video_id)
        
        if time.time() - start_time > 10:
            logging.warning(f"Réponse lente pour la vidéo {video_id}")
        
        if not info:
            return jsonify({
                'success': False,
                'error': 'Impossible d\'obtenir l\'URL audio'
            }), 404
            
        if not info.get('url'):
            return jsonify({
                'success': False,
                'error': 'URL audio non trouvée'
            }), 404

        return jsonify({
            'success': True,
            'audio_url': info['url'],
            'title': info['title']
        })
        
    except Exception as e:
        logging.error(f"Erreur de lecture pour {video_id}: {str(e)}")
        error_type = type(e).__name__
        return jsonify({
            'success': False,
            'error': 'Erreur lors de la lecture',
            'type': error_type,
            'details': str(e)
        }), 500

@app.route('/save-theme', methods=['POST'])
def save_theme():
    theme_data = request.json
    if theme_data:
        try:
            with open(THEME_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'theme': theme_data.get('theme', 'default'),
                    'primaryColor': theme_data.get('primaryColor', '#fca606')
                }, f, indent=2)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})
    return jsonify({'success': False, 'error': 'Invalid theme data'})

@app.route('/get-theme', methods=['GET'])
def get_theme():
    try:
        with open(THEME_FILE, 'r', encoding='utf-8') as f:
            theme_data = json.load(f)
        return jsonify({'success': True, 'theme': theme_data})
    except:
        default_theme = {
            'theme': 'default',
            'primaryColor': '#fca606'
        }
        with open(THEME_FILE, 'w', encoding='utf-8') as f:
            json.dump(default_theme, f, indent=2)
        return jsonify({'success': True, 'theme': default_theme})

@app.route('/import-spotify', methods=['POST'])
def import_spotify_playlist():
    spotify_url = request.json.get('url')
    if not spotify_url:
        return jsonify({'success': False, 'error': 'URL manquante'})
    
    try:
        playlist_id = spotify_url.split('playlist/')[1].split('?')[0]
        
        try:
            playlist = sp.playlist(playlist_id)
            playlist_name = playlist['name']
            
            if playlist_name in playlists:
                playlist_name = f"{playlist_name}_{len(playlists)}"
            
            playlists[playlist_name] = []
            
            results = sp.playlist_tracks(playlist_id)
            tracks = results['items']
            
            while results['next']:
                results = sp.next(results)
                tracks.extend(results['items'])
            
            for item in tracks:
                track = item['track']
                if not track:
                    continue
                
                artists = [artist['name'] for artist in track['artists']]
                search_query = f"{track['name']} {' '.join(artists)}"
                
                with YoutubeDL(ydl_opts) as ydl:
                    try:
                        result = ydl.extract_info(f"ytsearch1:{search_query}", download=False)
                        if 'entries' in result and result['entries']:
                            video = result['entries'][0]
                            playlists[playlist_name].append({
                                'id': video['id'],
                                'title': f"{track['name']} - {', '.join(artists)}"
                            })
                            logging.info(f"Ajouté: {track['name']}")
                    except Exception as e:
                        logging.error(f"Erreur YouTube pour {search_query}: {str(e)}")
                        continue
            
            save_playlists(playlists)
            return jsonify({
                'success': True,
                'message': f'Playlist "{playlist_name}" importée ({len(playlists[playlist_name])} morceaux)'
            })
            
        except spotipy.SpotifyException as e:
            logging.error(f"Erreur Spotify: {str(e)}")
            return jsonify({'success': False, 'error': 'Erreur d\'accès à Spotify'})
            
    except Exception as e:
        logging.error(f"Erreur d'importation: {str(e)}")
        return jsonify({'success': False, 'error': 'Erreur lors de l\'importation'})

@app.route('/playlist', methods=['POST'])
def create_playlist():
    data = request.json
    playlist_name = data.get('name')
    if playlist_name:
        if playlist_name not in playlists:
            playlists[playlist_name] = []
            save_playlists(playlists)
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Playlist already exists'})
    return jsonify({'success': False, 'error': 'Invalid playlist name'})

@app.route('/playlists', methods=['GET'])
def get_playlists():
    return jsonify({
        'success': True,
        'playlists': playlists
    })

@app.route('/playlist/<name>/add', methods=['POST'])
def add_to_playlist(name):
    if not name or name not in playlists:
        return jsonify({'success': False, 'error': 'Invalid playlist name'})
    
    try:
        song = request.json
        if not song or 'id' not in song or 'title' not in song:
            return jsonify({'success': False, 'error': 'Invalid song data'})
            
        if not any(s['id'] == song['id'] for s in playlists[name]):
            playlists[name].append(song)
            save_playlists(playlists)
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Song already in playlist'})
    except Exception as e:
        logging.error(f"Error adding to playlist: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to add song'})

@app.route('/playlist/<name>', methods=['GET'])
def get_playlist(name):
    if name in playlists:
        return jsonify({
            'success': True,
            'playlist': playlists[name],
            'name': name
        })
    return jsonify({'success': False, 'error': 'Playlist not found'})

@app.route('/playlist/<name>/remove/<song_id>', methods=['DELETE'])
def remove_from_playlist(name, song_id):
    if name in playlists:
        playlists[name] = [song for song in playlists[name] if song['id'] != song_id]
        save_playlists(playlists)
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Playlist not found'})

@app.route('/playlist/<name>', methods=['DELETE'])
def delete_playlist(name):
    if name in playlists:
        del playlists[name]
        save_playlists(playlists)
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Playlist not found'})

@app.route('/playlist/<name>/rename', methods=['PUT'])
def rename_playlist(name):
    try:
        if name not in playlists:
            return jsonify({'success': False, 'error': 'Playlist non trouvée'})
        
        data = request.json
        new_name = data.get('newName')
        
        if not new_name:
            return jsonify({'success': False, 'error': 'Nouveau nom invalide'})
            
        if new_name in playlists:
            return jsonify({'success': False, 'error': 'Ce nom de playlist existe déjà'})
            
        # Sauvegarder les données de la playlist
        playlist_data = playlists[name]
        # Supprimer l'ancienne clé
        del playlists[name]
        # Créer la nouvelle clé avec les mêmes données
        playlists[new_name] = playlist_data
        
        save_playlists(playlists)
        
        return jsonify({
            'success': True,
            'oldName': name,
            'newName': new_name
        })
    except Exception as e:
        logging.error(f"Error renaming playlist: {str(e)}")
        return jsonify({'success': False, 'error': 'Erreur lors du renommage'})

@app.route('/get-stats', methods=['GET'])
def get_stats():
    try:
        current_stats = load_stats()
        return jsonify({'success': True, 'stats': current_stats})
    except Exception as e:
        logging.error(f"Error in get_stats: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/save-stats', methods=['POST'])
def save_stats_route():
    try:
        new_stats = request.json
        if not new_stats:
            raise ValueError("No stats data provided")
        
        current_stats = load_stats()
        current_stats.update({
            'totalSongsPlayed': new_stats.get('totalSongsPlayed', current_stats['totalSongsPlayed']),
            'totalHoursPlayed': new_stats.get('totalHoursPlayed', current_stats['totalHoursPlayed'])
        })
        
        if save_stats(current_stats):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Failed to save stats'})
    except Exception as e:
        logging.error(f"Error in save_stats_route: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

# Nouvelles fonctions utilitaires
def clean_filename(filename):
    """Nettoie un nom de fichier des caractères invalides"""
    return "".join(c for c in filename if c.isalnum() or c in (' ', '-', '_')).strip()

def get_file_size(filepath):
    """Retourne la taille d'un fichier en Mo"""
    try:
        return os.path.getsize(filepath) / (1024 * 1024)
    except:
        return 0

def cleanup_downloads():
    """Nettoie les fichiers téléchargés trop anciens"""
    try:
        for file in os.listdir(DOWNLOAD_DIR):
            filepath = os.path.join(DOWNLOAD_DIR, file)
            if time.time() - os.path.getctime(filepath) > 86400:  # 24h
                os.remove(filepath)
    except Exception as e:
        logging.error(f"Erreur nettoyage downloads: {e}")

@app.route('/playlist/<name>/shuffle', methods=['POST'])
def shuffle_playlist(name):
    try:
        if name not in playlists:
            return jsonify({'success': False, 'error': 'Playlist non trouvée'})
            
        import random
        shuffled = playlists[name].copy()
        random.shuffle(shuffled)
        playlists[name] = shuffled
        save_playlists(playlists)
        
        return jsonify({'success': True, 'playlist': shuffled})
    except Exception as e:
        logging.error(f"Erreur shuffle: {e}")
        return jsonify({'success': False, 'error': str(e)})

# Amélioration de la gestion des erreurs
@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({
        'success': False,
        'error': 'Fichier trop volumineux'
    }), 413

@app.errorhandler(429)
def too_many_requests(error):
    return jsonify({
        'success': False,
        'error': 'Trop de requêtes'
    }), 429

def start_server():
    """Démarre le serveur Flask"""
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)

# Amélioration de la fonction principale
if __name__ == '__main__':
    try:
        # Nettoyage initial
        cleanup_downloads()
        cleanup_cache()
        
        # Démarrage du serveur
        server_thread = threading.Thread(target=start_server, daemon=True)
        server_thread.start()

        # Configuration de la fenêtre
        window = webview.create_window(
            "OpenPy Music Player",
            "http://127.0.0.1:5000",
            width=1200,
            height=800,
            resizable=True,
            min_size=(800, 600)
        )
        
        # Gestionnaire de fermeture
        def on_closing():
            cleanup_downloads()
            cleanup_cache()
            os._exit(0)
            
        window.events.closing += on_closing
        webview.start(debug=False)
        sys.exit()
        
    except Exception as e:
        logging.error(f"Erreur fatale: {e}")
        sys.exit(1)