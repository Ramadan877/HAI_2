#Version 2
from flask import Flask, request, render_template, jsonify, session, send_from_directory, Response, stream_with_context
from werkzeug.utils import secure_filename
from flask_cors import CORS 
import openai
from difflib import SequenceMatcher
import re
import requests
import os.path
from gtts import gTTS
import whisper
import warnings
warnings.filterwarnings("ignore", category=SyntaxWarning)
try:
    from pydub import AudioSegment
except ImportError as e:
    import warnings
    warnings.filterwarnings("ignore")
    
    class MockAudioSegment:
        @classmethod
        def empty(cls):
            return cls()
        
        @classmethod
        def from_mp3(cls, file):
            return cls()
        
        @classmethod
        def from_file(cls, file):
            return cls()
        
        def __add__(self, other):
            return self
    
    AudioSegment = MockAudioSegment
    print("Warning: Using mock AudioSegment due to audioop compatibility issues")

import json
import uuid
from datetime import datetime
import logging
import re
import gc
import threading
from functools import lru_cache
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from database import db, Participant, Session, Interaction, Recording, UserEvent
import uuid

load_dotenv()


def save_interaction_to_db(session_id, speaker, concept_name, message, attempt_number=1):
    """Save interaction to database."""
    try:
        database_url = os.environ.get('DATABASE_URL')
        if not database_url:
            print("Database not configured, skipping interaction save")
            return
            
        interaction = Interaction(
            session_id=session_id,
            speaker=speaker,
            concept_name=concept_name,
            message=message,
            attempt_number=attempt_number
        )
        db.session.add(interaction)
        db.session.commit()
    except Exception as e:
        print(f"Error saving interaction: {str(e)}")
        try:
            db.session.rollback()
        except:
            pass

def save_recording_to_db(session_id, recording_type, file_path, original_filename, 
                        file_size, concept_name=None, attempt_number=None):
    """Save recording metadata to database."""
    try:
        if not db or not os.environ.get('DATABASE_URL'):
            print("Database not configured, skipping recording save")
            return None
            
        recording = Recording(
            session_id=session_id,
            recording_type=recording_type,
            file_path=file_path,
            original_filename=original_filename,
            file_size=file_size,
            concept_name=concept_name,
            attempt_number=attempt_number
        )
        db.session.add(recording)
        db.session.commit()
        return recording.id
    except Exception as e:
        print(f"Error saving recording: {str(e)}")
        try:
            db.session.rollback()
        except:
            pass
        return None

def create_session_record(participant_id, trial_type, version):
    """Create a new session record."""
    try:
        if not db or not os.environ.get('DATABASE_URL'):
            print("Database not configured, skipping session creation")
            return None
            
        participant = Participant.query.filter_by(participant_id=participant_id).first()
        if not participant:
            participant = Participant(participant_id=participant_id)
            db.session.add(participant)
        
        session_id = f"{participant_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
        session_record = Session(
            session_id=session_id,
            participant_id=participant_id,
            trial_type=trial_type,
            version=version
        )
        db.session.add(session_record)
        db.session.commit()
        return session_id
    except Exception as e:
        print(f"Error creating session: {str(e)}")
        try:
            db.session.rollback()
        except:
            pass
        return None


def save_audio_with_cloud_backup(audio_data, filename, session_id, recording_type, concept_name=None, attempt_number=None):
    """Save audio locally."""
    try:
        local_path = os.path.join(UPLOAD_FOLDER, filename)
        
        if hasattr(audio_data, 'save'):
            audio_data.save(local_path)
        else:
            with open(local_path, 'wb') as f:
                f.write(audio_data)
        
        return local_path, None
    except Exception as e:
        print(f"Error in save_audio_with_cloud_backup: {str(e)}")
        return None, None

def log_interaction_to_db_only(speaker, concept_name, message, attempt_number=1):
    """Log interaction to database only - separate from file logging."""
    try:
        session_id = session.get('session_id')
        if session_id:
            save_interaction_to_db(session_id, speaker, concept_name, message, attempt_number)
        else:
            print("Warning: No session_id found for database logging")
    except Exception as e:
        print(f"Error logging interaction to database: {str(e)}")

def backup_existing_files_to_cloud():
    """This function is no longer needed but kept for compatibility."""
    return False

def initialize_session_in_db():
    """Initialize session in database when user starts - call this in set_trial_type."""
    try:
        participant_id = session.get('participant_id')
        trial_type = session.get('trial_type')
        
        if participant_id and trial_type:
            session_id = create_session_record(participant_id, trial_type, "V2")
            if session_id:
                session['session_id'] = session_id
                return session_id
    except Exception as e:
        print(f"Error initializing session in database: {str(e)}")
    return None

def get_participant_folder(participant_id, trial_type):
    """Get or create the participant's folder structure."""
    trial_folder_map = {
        'Trial_1': 'main_task_1',
        'Trial_2': 'main_task_2', 
        'Test': 'test_task'
    }
    
    trial_folder_name = trial_folder_map.get(trial_type, trial_type.lower())
    participant_folder = os.path.join(USER_DATA_BASE_FOLDER, str(participant_id), trial_folder_name)
    screen_recordings_folder = os.path.join(participant_folder, 'Screen Recordings')

    os.makedirs(participant_folder, exist_ok=True)
    os.makedirs(screen_recordings_folder, exist_ok=True)

    return {
        'participant_folder': participant_folder,
        'screen_recordings_folder': screen_recordings_folder
    }


OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

app = Flask(__name__)
CORS(app)  
try:
    from flask_compress import Compress
    Compress(app)
except Exception:
    pass

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'connect_args': {'sslmode': 'require'}
}
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
app.secret_key = os.environ.get('SECRET_KEY', 'fallback-secret-key')

app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB limit for long recordings

db.init_app(app)

with app.app_context():
    db.create_all()
    executor = ThreadPoolExecutor(max_workers=5)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.normpath(os.path.join(BASE_DIR, 'uploads'))
CONCEPT_AUDIO_FOLDER = os.path.normpath(os.path.join(UPLOAD_FOLDER, 'concept_audio'))
INTRO_AUDIO_FOLDER = os.path.normpath(os.path.join(UPLOAD_FOLDER, 'intro_audio'))
USER_DATA_BASE_FOLDER = os.path.normpath(os.path.join(UPLOAD_FOLDER, 'User Data'))
STATIC_FOLDER = os.path.normpath(os.path.join(BASE_DIR, 'static'))

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['CONCEPT_AUDIO_FOLDER'] = CONCEPT_AUDIO_FOLDER
app.config['INTRO_AUDIO_FOLDER'] = INTRO_AUDIO_FOLDER
app.config['USER_DATA_BASE_FOLDER'] = USER_DATA_BASE_FOLDER

os.makedirs(CONCEPT_AUDIO_FOLDER, exist_ok=True)
os.makedirs(INTRO_AUDIO_FOLDER, exist_ok=True)
os.makedirs(USER_DATA_BASE_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)
    
def check_paths():
    """Verify all required paths exist and are writable."""
    paths = [
        app.config['UPLOAD_FOLDER'], 
        app.config['CONCEPT_AUDIO_FOLDER'],
        app.config['INTRO_AUDIO_FOLDER'],
        app.config['USER_DATA_BASE_FOLDER'],
        STATIC_FOLDER
    ]
    for path in paths:
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
        if not os.access(path, os.W_OK):
            return False
    return True

def allowed_file(filename):
    """Check if the file type is allowed."""
    ALLOWED_EXTENSIONS = {'wav', 'mp3', 'webm'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

whisper_model = None

def get_whisper_model():
    global whisper_model
    if whisper_model is None:
        try:
            print("Loading Whisper model...")
            whisper_model = whisper.load_model("small")
            print("Whisper model loaded successfully")
        except Exception as e:
            print(f"Failed to load Whisper model: {str(e)}")
    return whisper_model

@lru_cache(maxsize=32)
def cached_transcribe(audio_file_path_hash):
    """Helper function to enable caching of transcription results"""
    model = get_whisper_model()
    if model:
        result = model.transcribe(audio_file_path_hash)
        return result["text"]
    return "Transcription failed."

def speech_to_text(audio_file_path):
    """Convert audio to text using OpenAI Whisper API or local fallback."""
    try:
        audio_file_path = os.path.normpath(audio_file_path)
        
        print(f"Processing audio file: {audio_file_path}")
        print(f"File exists: {os.path.exists(audio_file_path)}")
        print(f"File size: {os.path.getsize(audio_file_path) if os.path.exists(audio_file_path) else 'File not found'}")
        
        if not os.path.exists(audio_file_path):
            print(f"Audio file not found at: {audio_file_path}")
            return "Audio file not found"
            
        with open(audio_file_path, "rb") as audio_file:
            transcript = openai.Audio.transcribe(
                model="whisper-1",
                file=audio_file
            )
            return transcript["text"]
    except Exception as e:
        print(f"Error using OpenAI Whisper API: {str(e)}")
        print("Falling back to local Whisper model...")
        
        try:
            model = get_whisper_model()
            if model:
                result = model.transcribe(audio_file_path)
                return result["text"]
            else:
                return "Whisper model not available"
        except Exception as e2:
            print(f"Error using local Whisper model: {str(e2)}")
            return "Audio processing failed"

def get_interaction_id(participant_id=None):
    """Generate a unique interaction ID based on timestamp."""
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    if participant_id:
        return f"{participant_id}_{timestamp}"
    return timestamp

def create_user_folders(participant_id, trial_type):
    """Create user-specific folders for audio and logs."""
    trial_folder_map = {
        'Trial_1': 'main_task_1',
        'Trial_2': 'main_task_2',
        'Test': 'test_task'
    }
    
    trial_folder_name = trial_folder_map.get(trial_type, trial_type.lower())
    
    participant_folder = os.path.normpath(os.path.join(USER_DATA_BASE_FOLDER, str(participant_id)))
    os.makedirs(participant_folder, exist_ok=True)
    
    task_folder = os.path.normpath(os.path.join(participant_folder, trial_folder_name))
    os.makedirs(task_folder, exist_ok=True)
    
    screen_recordings_dir = os.path.normpath(os.path.join(task_folder, 'Screen Recordings'))
    os.makedirs(screen_recordings_dir, exist_ok=True)
    
    return task_folder

def get_audio_filename(prefix, participant_id, trial_type, attempt_number, extension='.mp3'):
    """Generate a unique audio filename with participant ID, concept name, and attempt number."""
    import inspect
    frame = inspect.currentframe().f_back
    concept_name = frame.f_locals.get('concept_name', None)
    concept_part = f"_{secure_filename(concept_name)}" if concept_name else ""
    return f"{prefix}{concept_part}_{attempt_number}_{participant_id}{extension}"

def get_general_audio_filename(prefix, concept_name=None, extension='.mp3'):
    """Generate a filename for general audio (intro, concept intros)."""
    name_part = f"{prefix}_{secure_filename(concept_name)}" if concept_name else prefix
    return f"{name_part}{extension}"


def synthesize_with_openai(text, voice='alloy', fmt='mp3'):
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise RuntimeError('OpenAI API key not configured')
    url = 'https://api.openai.com/v1/audio/speech'
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    payload = {'model': 'gpt-4o-mini-tts', 'voice': voice, 'input': text}
    resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
    resp.raise_for_status()
    audio_bytes = resp.content
    content_type = 'audio/mpeg' if fmt.lower() in ('mp3','mpeg') else 'audio/webm'
    return audio_bytes, content_type


def ssml_wrap(text, rate='0%', pitch='0%', break_ms=250):
    """Wrap text in a small SSML template to improve TTS prosody.
    This escapes XML special chars and inserts small breaks after punctuation.
    If SSML generation fails, return original text.
    """
    try:
        def esc(t):
            return (t.replace('&', '&amp;')
                     .replace('<', '&lt;')
                     .replace('>', '&gt;')
                     .replace('"', '&quot;')
                     .replace("'", '&apos;'))

        safe_text = esc(text)
        import re
        safe_text = re.sub(r'([\.\?\!])\s+', r"\1 <break time=\"%dms\"/> " % break_ms, safe_text)
        safe_text = re.sub(r',\s+', r', <break time=\"%dms\"/> ' % int(break_ms/2), safe_text)

        ssml = f"<speak><prosody rate='-{abs(int(rate.strip('%') if isinstance(rate,str) and rate.endswith('%') else 0))}%' pitch='{pitch}'>" + safe_text + "</prosody></speak>"
        return ssml
    except Exception as e:
        print('SSML wrap failed:', str(e))
        return text


def clean_for_tts(text):
    """Sanitize text before sending to plain-text TTS engines.
    Removes any XML/HTML-like tags (including SSML <break/> tags) and
    collapses multiple whitespace/newlines so that engines like gTTS do
    not attempt to speak markup as literal words (which can produce
    phrases like "line break" or similar).
    """
    try:
        if not text:
            return text
        cleaned = re.sub(r"<[^>]+>", " ", text)
        cleaned = cleaned.replace('\u200b', ' ')
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned
    except Exception as e:
        print(f"clean_for_tts failed: {e}")
        return text


@app.route('/synthesize', methods=['POST'])
def synthesize():
    try:
        data = request.get_json() or request.form
        text = data.get('text') if data else None
        if not text:
            return jsonify({'error': 'No text provided'}), 400
        voice = data.get('voice', 'alloy')
        fmt = data.get('format', 'mp3')
        try:
            ssml_text = ssml_wrap(text, rate='5%', pitch='0%', break_ms=220)
            audio_bytes, content_type = synthesize_with_openai(ssml_text, voice=voice, fmt=fmt)
            return (audio_bytes, 200, {'Content-Type': content_type, 'Content-Disposition': 'inline; filename="tts.' + fmt + '"'})
        except Exception as openai_err:
            print('OpenAI TTS failed or rejected SSML, falling back to gTTS:', str(openai_err))
        try:
            from io import BytesIO
            bio = BytesIO()
            cleaned_for_tts = clean_for_tts(ssml_text) or clean_for_tts(text)
            tts = gTTS(text=cleaned_for_tts, lang='en')
            tts.write_to_fp(bio)
            bio.seek(0)
            return (bio.read(), 200, {'Content-Type': 'audio/mpeg', 'Content-Disposition': 'inline; filename="tts.mp3"'})
        except Exception as e:
            print('gTTS fallback failed:', str(e))
            return jsonify({'error': 'TTS synthesis failed'}), 500
    except Exception as e:
        print('Synthesize endpoint error:', str(e))
        return jsonify({'error': str(e)}), 500

def get_log_filename(participant_id, trial_type):
    """Generate log filename for the current session."""
    return f"conversation_log_{participant_id}.txt"

def initialize_log_file(interaction_id, participant_id, trial_type):
    """Initialize a new log file with header information."""
    if not participant_id or not trial_type:
        return False
        
    task_folder = create_user_folders(participant_id, trial_type)
    log_filename = get_log_filename(participant_id, trial_type)
    log_file_path = os.path.join(task_folder, log_filename)

    try:
        with open(log_file_path, "w", encoding="utf-8") as file:
            file.write("=" * 80 + "\n")
            file.write("CONVERSATION LOG\n")
            file.write("=" * 80 + "\n\n")
            file.write(f"PARTICIPANT ID: {participant_id}\n")
            file.write(f"INTERACTION ID: {interaction_id}\n")
            file.write(f"VERSION: 2\n")
            file.write(f"TRIAL: {trial_type}\n")
            file.write(f"TIMESTAMP: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            file.write("\n" + "-" * 80 + "\n\n")
        
        app.config['CURRENT_LOG_FILE'] = log_file_path
        return True
    except Exception as e:
        print(f"Error initializing log file: {str(e)}")
        return False

def log_interaction(speaker, concept_name, text):
    """Log the interaction to the current log file with timestamp."""
    try:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        current_log_file = app.config.get('CURRENT_LOG_FILE')
        
        if not current_log_file:
            participant_id = session.get('participant_id')
            trial_type = session.get('trial_type')
            if not participant_id or not trial_type:
                return False
                
            interaction_id = session.get('interaction_id', get_interaction_id())
            initialize_log_file(interaction_id, participant_id, trial_type)
            current_log_file = app.config.get('CURRENT_LOG_FILE')
        
        with open(current_log_file, "a", encoding="utf-8") as file:
            file.write(f"[{timestamp}] {speaker}: {text}\n\n")
            
        print(f"Interaction logged: {speaker} in file {current_log_file}")
        return True
    except Exception as e:
        print(f"Error logging interaction: {str(e)}")
        return False

last_concept_change = {
    'slide_number': None,
    'concept_name': None,
    'timestamp': None
}

@app.route('/change_concept', methods=['POST'])
def change_concept():
    data = request.get_json()
    slide_number = data.get('slide_number', 'unknown')
    concept_name = data.get('concept_name', 'unknown')

    if 'concept_attempts' not in session:
        session['concept_attempts'] = {}
    session['concept_attempts'][concept_name] = 0
    session.modified = True

    print(f"Concept changed to: {concept_name}")
    print(f"Reset attempt count for concept: {concept_name}")
    print("Current session state:", dict(session))

    message = f"User navigated to slide [{slide_number}] with the concept: [{concept_name}]"
    log_interaction("SYSTEM", concept_name, message)

    return jsonify({'status': 'success', 'message': 'Navigation and concept change logged'})
    
def generate_audio_async(text, file_path):
    """Generate audio asynchronously"""
    return executor.submit(generate_audio, text, file_path)

def generate_audio(text, file_path):
    """Generate speech (audio) from the provided text.

    Prefer OpenAI TTS (synthesize_with_openai) and save the returned bytes to disk.
    If OpenAI TTS is unavailable or fails, fall back to the existing gTTS + pydub logic.
    Returns True on success, False on failure.
    """
    try:
        try:
            audio_bytes, content_type = synthesize_with_openai(text, voice='alloy', fmt='mp3')
            if audio_bytes:
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, 'wb') as f:
                    f.write(audio_bytes)
                print(f"Audio file (OpenAI TTS) saved: {file_path} (content_type={content_type})")
                return True
        except Exception as openai_err:
            print(f"OpenAI TTS not available or failed: {openai_err}. Falling back to gTTS.")

        sanitized_text = clean_for_tts(text)
        if len(sanitized_text) > 500:
            chunks = [sanitized_text[i:i+500] for i in range(0, len(sanitized_text), 500)]
            temp_files = []

            for i, chunk in enumerate(chunks):
                temp_file = f"{file_path}.part{i}.mp3"
                tts = gTTS(text=chunk, lang='en')
                tts.save(temp_file)
                temp_files.append(temp_file)

            combined = AudioSegment.empty()
            for temp in temp_files:
                segment = AudioSegment.from_mp3(temp)
                combined += segment

            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            combined.export(file_path, format="mp3")

            for temp in temp_files:
                try:
                    os.remove(temp)
                except:
                    pass
        else:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            tts = gTTS(text=text, lang='en')
            tts.save(file_path)

        if os.path.exists(file_path):
            print(f"Audio file successfully saved (gTTS fallback): {file_path}")
            return True
        else:
            print(f"Failed to save audio file: {file_path}")
            return False
    except Exception as e:
        print(f"Error generating audio: {str(e)}")
        return False



@app.route('/list_recent_recordings')
def list_recent_recordings():
    """Return latest N recordings from DB with file existence checks."""
    try:
        n = int(request.args.get('n', 20))
        recs = Recording.query.order_by(Recording.created_at.desc()).limit(n).all()
        out = []
        base = app.config.get('USER_DATA_BASE_FOLDER', os.path.join(app.root_path, 'uploads', 'User Data'))
        for r in recs:
            fp = r.file_path or ''
            if fp and not os.path.isabs(fp):
                full = os.path.normpath(os.path.join(base, fp))
            else:
                full = fp
            exists = os.path.exists(full) if full else False
            size = os.path.getsize(full) if exists else None
            session_rec = Session.query.filter_by(session_id=r.session_id).first()
            participant_id = session_rec.participant_id if session_rec else None
            out.append({
                'id': r.id,
                'session_id': r.session_id,
                'participant_id': participant_id,
                'recording_type': r.recording_type,
                'file_path_db': r.file_path,
                'file_path_resolved': full,
                'exists': exists,
                'size': size,
                'created_at': r.created_at
            })
        return jsonify({'status': 'ok', 'recent_recordings': out})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/')
def home():
    """Render the home page."""
    session['interaction_id'] = get_interaction_id()
    session['trial_type'] = request.args.get('trial', 'Trial_1')    
    
    return render_template('index.html')

@app.route('/resources/<path:filename>')
def download_resource(filename):
    """Serve resources like PDF or video files from the resources folder."""
    return send_from_directory('resources', filename)


@lru_cache(maxsize=1)
def load_concepts():
    """Load concepts from a JSON file with caching."""
    try:
        with open("concepts.json", "r") as file:
            concepts = json.load(file)["concepts"]
            print(f"Loaded {len(concepts)} concepts successfully")
            return concepts
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Concepts file not found or invalid: {str(e)}. Creating default concepts...")
        concepts = [
            {
                "name": "Correlation",
                "golden_answer": "Correlation describes the strength and direction of a relationship between two variables, ranging from -1 to 1. A value close to 1 indicates a strong positive relationship, while a value close to -1 indicates a strong negative one. Importantly, correlation does not imply causation. It only shows that two variables change together. A third variable may influence both, which is why identifying extraneous variables is essential."
            },
            {
                "name": "Confounders",
                "golden_answer": "A confounder is a variable that is related to both the independent and dependent variables and can create a false impression of a relationship between them. It can make it seem like X causes Y, when in reality the confounder might be responsible for the effect. For example, physical activity may influence both the likelihood of following a diet and the amount of weight lost."
            },
            {
                "name": "Moderators",
                "golden_answer": "A moderator affects the strength or direction of the relationship between an independent and a dependent variable. It helps researchers understand under what conditions or for whom an effect occurs. For instance, stress may change how effective a diet is in producing weight loss by altering eating habits or metabolism. Identifying moderators can provide more nuanced insights into how variables interact."
            }
        ]
        
        try:
            with open("concepts.json", "w") as file:
                json.dump({"concepts": concepts}, file, indent=4)
            print("Default concepts created and saved successfully")
        except Exception as write_err:
            print(f"Error saving default concepts: {str(write_err)}")
        
        return concepts


@app.route('/set_context', methods=['POST'])
def set_context():
    """Set the context for a specific concept from the provided material."""
    concept_name = request.form.get('concept_name')  
    concepts = load_concepts()
    
    selected_concept = next((c for c in concepts if c["name"] == concept_name), None)

    if not selected_concept:
        return jsonify({'error': 'Invalid concept selection'})

    session['concept_name'] = selected_concept["name"]
    session['golden_answer'] = selected_concept["golden_answer"]
    session['attempt_count'] = 0
    
    log_interaction("SYSTEM", selected_concept["name"], 
                    f"Context set for concept: {selected_concept['name']}")

    return jsonify({'message': f'Context set for {selected_concept["name"]}.'})


@app.route('/log_interaction_event', methods=['POST'])
def log_interaction_event():
    """Log user interaction events like chat window open/close, audio controls, etc."""
    try:
        data = request.get_json()
    except Exception:
        data = request.form

    event_type = data.get('event_type') if data else None
    event_details = data.get('details', {}) if data else {}
    concept_name = data.get('concept_name') if data else None

    message = f"User {event_type}"
    if event_type == "CHAT_WINDOW":
        message = f"User {event_details.get('action', 'unknown')} the chat window"
    elif event_type == "AUDIO_PLAYBACK":
        message = f"User {event_details.get('action', 'unknown')} audio playback at {event_details.get('timestamp', '0')} seconds"
    elif event_type == "AUDIO_SPEED":
        message = f"User changed audio speed to {event_details.get('speed', '1')}x"
    elif event_type == "RECORDING":
        action = event_details.get('action', 'unknown')
        timestamp = event_details.get('timestamp', '')
        if action == 'started':
            message = f"User started recording at {timestamp}"
        elif action == 'stopped':
            message = f"User stopped recording at {timestamp}"
        elif action == 'submitted':
            blob_size = event_details.get('blobSize', 'unknown')
            duration = event_details.get('duration', 'unknown')
            message = f"User submitted recording (size: {blob_size} bytes, duration: {duration}s) at {timestamp}"

    log_interaction("SYSTEM", concept_name, message)

    return jsonify({'status': 'success', 'message': 'Event logged successfully'})

@app.route('/get_intro_audio', methods=['GET'])
def get_intro_audio():
    """Generate the introductory audio message for the chatbot."""
    participant_id = session.get('participant_id')
    interaction_id = session.get('interaction_id')

    intro_text = "Hello, let us begin the self-explanation journey! We'll be exploring the concept of Extraneous Variables, focusing on Correlation, Confounders, and Moderators. Please go through each concept and explain what you understand about them in your own words!"
    intro_audio_filename = get_general_audio_filename('intro_message')
    intro_audio_path = os.path.join(app.config['INTRO_AUDIO_FOLDER'], intro_audio_filename)

    if not os.path.exists(intro_audio_path): 
        generate_audio(intro_text, intro_audio_path)
        log_interaction("AI", "Introduction", intro_text)
    
    if os.path.exists(intro_audio_path):
        intro_audio_url = f"/uploads/intro_audio/{intro_audio_filename}"
        return jsonify({'intro_audio_url': intro_audio_url})
    else:
        return jsonify({'error': 'Failed to generate introduction audio'}), 500

@app.route('/get_concept_audio/<concept_name>', methods=['GET'])
def get_concept_audio(concept_name):
    """Generate concept introduction audio message."""
    try:
        interaction_id = session.get('interaction_id', get_interaction_id())
        safe_concept = secure_filename(concept_name)
        
        concept_audio_filename = get_general_audio_filename('concept_intro', concept_name=safe_concept)
        concept_audio_path = os.path.join(app.config['CONCEPT_AUDIO_FOLDER'], concept_audio_filename)
        
        concept_intro_text = f"Now go through this concept of {concept_name}, and try explaining what you understood from this concept in your own words!"
        
        if not os.path.exists(concept_audio_path) or \
           getattr(get_concept_audio, 'last_concept', None) != concept_name: 
            
            success = generate_audio(concept_intro_text, concept_audio_path)
            if not success:
                return jsonify({'error': 'Failed to generate audio'}), 500
                
            log_interaction("AI", concept_name, concept_intro_text)
            get_concept_audio.last_concept = concept_name
        
        if os.path.exists(concept_audio_path):
            return send_from_directory(
                app.config['CONCEPT_AUDIO_FOLDER'], 
                concept_audio_filename,
                mimetype='audio/mpeg'
            )
        else:
            return jsonify({'error': 'Audio file not found'}), 404
            
    except Exception as e:
        print(f"Error in get_concept_audio: {str(e)}")
        return jsonify({'error': str(e)}), 500

get_concept_audio.last_concept = None

@app.route('/submit_message', methods=['POST'])
def submit_message():
    """Handle the submission of user messages and generate AI responses."""
    user_message = request.form.get('message')
    audio_file = request.files.get('audio')
    concept_name = request.form.get('concept_name')
    participant_id = session.get('participant_id')
    trial_type = session.get('trial_type')
    interaction_id = session.get('interaction_id', get_interaction_id())

    if not participant_id or not trial_type:
        return jsonify({'error': 'Missing participant ID or trial type'}), 400

    print(f"Received concept from frontend: {concept_name}")
      
    if not user_message and not audio_file:
        print("Error: No message or audio received!")  
        return jsonify({'error': 'Message or audio is required.'})

    if not concept_name:
        print("Error: No concept detected!")  
        return jsonify({'error': 'Concept not detected.'})

    concepts = load_concepts()
    selected_concept = next((c for c in concepts if c["name"] == concept_name), None)

    if not selected_concept:
        print("Error: Concept not found in system!")  
        return jsonify({'error': 'Concept not found.'})

    print(f"Using concept: {selected_concept}")

    if 'concept_attempts' not in session:
        session['concept_attempts'] = {}

    if concept_name not in session['concept_attempts']:
        session['concept_attempts'][concept_name] = 0

    current_attempt_count = session['concept_attempts'][concept_name]
    print(f"Current attempt count for {concept_name}: {current_attempt_count}")
    
    # Get conversation history for this concept
    conversation_history = session.get('conversation_history', {}).get(concept_name, [])

    if audio_file and getattr(audio_file, 'filename', None):
        task_folder = create_user_folders(participant_id, trial_type)
        user_audio_filename = get_audio_filename('user', participant_id, trial_type, current_attempt_count + 1, '.wav')
        audio_path = os.path.join(task_folder, user_audio_filename)
        
        print(f"Saving audio file to: {audio_path}")
        print(f"Directory exists: {os.path.exists(os.path.dirname(audio_path))}")
            
        os.makedirs(os.path.dirname(audio_path), exist_ok=True)
        audio_file.save(audio_path)
            
        if os.path.exists(audio_path):
            print(f"Audio file saved successfully at: {audio_path}")
            print(f"File size: {os.path.getsize(audio_path)}")
                
            try:
                audio = AudioSegment.from_file(audio_path)
                audio.export(audio_path, format="wav")
                print("Audio converted to WAV format")
            except Exception as e:
                print(f"Error converting audio: {str(e)}")
                
            user_message = speech_to_text(audio_path)
        else:
            print(f"Failed to save audio file at: {audio_path}")
            return jsonify({'error': 'Failed to save audio file'}), 500
    
    log_interaction("USER", concept_name, user_message)

    session['concept_attempts'][concept_name] = current_attempt_count + 1
    print(f"Updated attempt count for {concept_name}: {session['concept_attempts'][concept_name]}")

    # Pass zero-based attempt count to the generator (0 == first attempt)
    ai_response = generate_response(
        user_message,
        selected_concept["name"],
        selected_concept["golden_answer"],
        current_attempt_count,
        conversation_history
    )

    if not ai_response:
        print("Error: AI response generation failed!")  
        return jsonify({'error': 'AI response generation failed.'})

    print(f"AI Response: {ai_response}")

    ai_lower = (ai_response or "").lower()
    move_phrases = [
        "please move to the next concept",
        "move to the next concept",
        "move on to the next concept",
        "please move on to the next concept",
        "correct answer:"
    ]
    should_move = any(p in ai_lower for p in move_phrases)
    if should_move:
        try:
            session['concept_attempts'][concept_name] = 3
        except Exception:
            pass

    if 'conversation_history' not in session:
        session['conversation_history'] = {}
    if concept_name not in session['conversation_history']:
        session['conversation_history'][concept_name] = []
    
    session['conversation_history'][concept_name].append(f"User: {user_message}")
    session['conversation_history'][concept_name].append(f"AI: {ai_response}")
    
    if len(session['conversation_history'][concept_name]) > 10:
        session['conversation_history'][concept_name] = session['conversation_history'][concept_name][-10:]
    
    session.modified = True

    log_interaction("AI", concept_name, ai_response)
    
    try:
        log_interaction_to_db_only("USER", concept_name, user_message, current_attempt_count + 1)
        log_interaction_to_db_only("AI", concept_name, ai_response, current_attempt_count + 1)
    except Exception as e:
        print(f"Database logging failed, but continuing: {str(e)}")

    task_folder = create_user_folders(participant_id, trial_type)
    ai_response_filename = get_audio_filename('AI', participant_id, trial_type, current_attempt_count + 1)
    audio_response_path = os.path.join(task_folder, ai_response_filename)

    try:
        executor.submit(generate_audio, ai_response, audio_response_path)
    except Exception as e:
        print(f"Failed to start async audio generation: {e}")
    
    try:
        session_id = session.get('session_id')
        if session_id and audio_file and os.path.exists(audio_path):
            with open(audio_path, 'rb') as f:
                audio_data = f.read()
            save_audio_with_cloud_backup(
                audio_data, user_audio_filename, session_id, 
                'user_audio', concept_name, current_attempt_count + 1
            )
            
        if session_id and os.path.exists(audio_response_path):
            with open(audio_response_path, 'rb') as f:
                ai_audio_data = f.read()
            save_audio_with_cloud_backup(
                ai_audio_data, ai_response_filename, session_id, 
                'ai_audio', concept_name, current_attempt_count + 1
            )
    except Exception as e:
        print(f"Audio backup failed, but continuing: {str(e)}")

    trial_folder_map = {
        'Trial_1': 'main_task_1', 'Trial_2': 'main_task_2', 'Test': 'test_task'
    }
    trial_folder_name = trial_folder_map.get(trial_type, trial_type.lower())
    ai_audio_url = f"/uploads/UserData/{participant_id}/{trial_folder_name}/{ai_response_filename}"

    return jsonify({
        'response': ai_response,
        'ai_audio_url': ai_audio_url,
        'user_transcript': user_message,
        'attempt_count': session.get('concept_attempts', {}).get(concept_name, current_attempt_count + 1),
        'should_move_to_next': bool(should_move)
    })

def generate_response(user_message, concept_name, golden_answer, attempt_count, conversation_history=None):
    """Generate short, supportive feedback for a 3-attempt self-explanation loop."""

    import re
    import openai

    if not golden_answer or not concept_name:
        return (
            "I can’t provide feedback yet because the concept context isn’t set. "
            "Please make sure both the concept and golden answer are defined."
        )

    history_context = ""
    if conversation_history and len(conversation_history) > 0:
        history_context = "\nRecent conversation:\n" + "\n".join(conversation_history[-3:])

    # === 2️⃣ Simple Similarity Check ===
    # Normalize and compare similarity ratio between student's and golden answer
    def normalize(text):
        return re.sub(r'[^a-z0-9\s]', '', text.lower().strip())

    similarity = SequenceMatcher(None, normalize(user_message), normalize(golden_answer)).ratio()

    # If user explanation is very close or matches the golden answer (≥0.8 similarity),
    # acknowledge immediately and skip GPT generation
    if similarity >= 0.8:
        return (
            "Excellent — your explanation is clear and accurate. "
            "You’ve captured the main idea correctly. "
            "You can now move on to the next concept."
        )

    # ==== Base prompt ====
    base_prompt = f"""
    Context: {concept_name}
    Golden Answer: {golden_answer}
    Student Explanation: {user_message}
    {history_context}

    You are a concise, friendly tutor guiding a student to understand a concept.
    The tone should be supportive, motivating, and natural — not exaggerated or lengthy.

    Rules:
    - Keep your feedback under 3 sentences.
    - Be positive and instructive, not overly enthusiastic.
    - Never reveal or restate the golden answer before the third attempt.
    - When the student's explanation is fully correct (matches the golden answer in meaning):
        → Clearly confirm correctness and tell them to move on to the next concept.
    - When the explanation is partially correct:
        → Mention briefly what is right and what is missing or unclear. Give one short hint.
    - When the explanation is incorrect:
        → Identify one main misunderstanding and provide one small clue to rethink it.
    - On the third attempt:
        → If correct, confirm and tell them to move on.
        → If still incorrect, briefly give the correct explanation and tell them to move on.
    - Use plain English; no emojis, lists, or unnecessary formatting.
    """

    # ==== Attempt-level instruction ====
    if attempt_count == 0:
        user_prompt = (
            "This is the student's FIRST attempt. If not fully correct, provide general feedback "
            "and one broad hint about what might be missing. Encourage them to try again."
        )
    elif attempt_count == 1:
        user_prompt = (
            "This is the student's SECOND attempt. If still incomplete, point out the missing element "
            "or misconception but DO NOT reveal the correct answer. Encourage them for one last try."
        )
    elif attempt_count == 2:
        user_prompt = (
            "This is the student's THIRD and FINAL attempt. "
            "If correct, confirm and tell them to move to the next concept. "
            "If still incorrect, now briefly provide the correct explanation and guide them to move on."
        )
    else:
        user_prompt = (
            "The student has already completed three attempts. "
            "Acknowledge their effort and tell them to move to the next concept."
        )

    enforcement_system = (
        "Respond only in English. "
        "If the student's input is not in English, ask politely in English to repeat it in English."
    )

    non_english = re.compile(r"[\u0590-\u05FF\u0600-\u06FF\u0400-\u04FF\u0900-\u097F\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]")
    if non_english.search(user_message):
        return "Please repeat your explanation in English so I can provide feedback."

    messages = [
        {"role": "system", "content": enforcement_system},
        {"role": "system", "content": base_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=80,
            temperature=0.4,
        )
        ai_response = response.choices[0].message.content.strip()
        return ai_response

    except Exception as e:
        return f"Error generating AI response: {str(e)}"



    def detect_non_english(text):
        if not text:
            return False
        non_latin_regex = re.compile(r"[\u0590-\u05FF\u0600-\u06FF\u0400-\u04FF\u0900-\u097F\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]")
        return bool(non_latin_regex.search(text))

    if detect_non_english(user_message):
        return "Please repeat your explanation in English so I can provide feedback. This interaction uses English only."

    try:
        messages = [
            {"role": "system", "content": enforcement_system},
            {"role": "system", "content": base_prompt},
            {"role": "user", "content": user_prompt}
        ]

        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=80,
            temperature=0.4,
        )

        ai_response = response.choices[0].message.content
        attempt_count += 1
        session['attempt_count'] = attempt_count
        return ai_response
    except Exception as e:
        return f"Error generating AI response: {str(e)}"


@app.route('/stream_submit_message', methods=['POST'])
def stream_submit_message():
    """Streaming variant of /submit_message — returns partial text as it's generated."""
    try:
        participant_id = session.get('participant_id')
        trial_type = session.get('trial_type')
        if not participant_id or not trial_type:
            return jsonify({'status': 'error', 'message': 'Participant ID or trial type not found in session'}), 400

        concept_name = request.form.get('concept_name', '').strip()
        concepts = load_concepts()

        concept_found = False
        for concept in concepts:
            if concept.lower() == concept_name.lower():
                concept_name = concept
                concept_found = True
                break

        if not concept_found:
            return jsonify({'status': 'error', 'message': 'Concept not found'}), 400

        golden_answer = concepts[concept_name]['golden_answer']

        user_transcript = ''
        if 'audio' in request.files:
            audio_file = request.files['audio']
            if audio_file:
                folders = get_participant_folder(participant_id, trial_type)
                audio_filename = get_audio_filename('user', participant_id, 1)
                audio_path = os.path.join(folders['participant_folder'], audio_filename)
                audio_file.save(audio_path)
                try:
                    with open(audio_path, 'rb') as f:
                        user_transcript = openai.Audio.transcribe(model='whisper-1', file=f)['text']
                except Exception:
                    user_transcript = speech_to_text(audio_path)

        base_prompt = f"""
        Context: {concept_name}
        Golden Answer: {golden_answer}
        User Explanation: {user_transcript}
        """

        messages = [
            {"role": "system", "content": base_prompt},
            {"role": "user", "content": user_transcript}
        ]

        def generate():
            try:
                stream_resp = openai.ChatCompletion.create(
                    model='gpt-4o-mini',
                    messages=messages,
                    max_tokens=80,
                    temperature=0.4,
                    stream=True
                )

                accumulator = ''
                for event in stream_resp:
                    try:
                        token = ''
                        if isinstance(event, dict) and 'choices' in event:
                            ch = event['choices'][0]
                            if 'delta' in ch:
                                token = ch['delta'].get('content', '')
                            elif 'text' in ch:
                                token = ch.get('text', '')
                        elif hasattr(event, 'choices'):
                            try:
                                token = event.choices[0].delta.get('content', '')
                            except Exception:
                                token = ''

                        if token:
                            accumulator += token
                            yield token
                    except Exception:
                        continue

                yield '\n'
            except Exception as e:
                yield f"[error] {str(e)}"

        return Response(stream_with_context(generate()), content_type='text/plain; charset=utf-8')
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/uploads/concept_audio/<filename>')
def serve_concept_audio(filename):
    """Serve concept audio files from the concept_audio folder."""
    return send_from_directory(app.config['CONCEPT_AUDIO_FOLDER'], filename)

@app.route('/uploads/intro_audio/<filename>')
def serve_intro_audio(filename):
    """Serve intro audio files from the intro_audio folder."""
    return send_from_directory(app.config['INTRO_AUDIO_FOLDER'], filename)

@app.route('/uploads/UserData/<participant_id>/<trial_type_folder>/<filename>')
def serve_user_data_audio(participant_id, trial_type_folder, filename):
    """Serve user-specific audio files (user recordings and AI responses)."""
    try:
        base_path = os.path.normpath(os.path.join(app.config['USER_DATA_BASE_FOLDER'], participant_id, trial_type_folder))

        file_path = os.path.normpath(os.path.join(base_path, filename))
        
        if not os.path.exists(file_path):
            print(f"File not found: {file_path}") # Debug print
            return jsonify({'error': 'File not found'}), 404
            
        return send_from_directory(base_path, filename)
    except Exception as e:
        print(f"Error serving user data audio: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/pdf')
def serve_pdf():
    """Serve the PDF file for the current concept."""
    return send_from_directory('resources', 'Extraneous Variables.pdf')



@app.route('/set_trial_type', methods=['POST'])
def set_trial_type():
    """Set the trial type and participant ID for the session."""
    try:
        data = request.get_json()
        trial_type = data.get('trial_type')
        participant_id = data.get('participant_id')        

        if not trial_type or not participant_id:
            return jsonify({
                'status': 'error',
                'message': 'Missing trial type or participant ID'
            }), 400

        valid_types = ["Trial_1", "Trial_2", "Test"]
        if trial_type not in valid_types:
            print(f"Invalid trial type: {trial_type}")
            return jsonify({
                "error": "Invalid trial type",
                "received_data": data
            }), 400
        
        old_trial_type = session.get('trial_type', 'None')

        interaction_id = get_interaction_id(participant_id)

        session['trial_type'] = trial_type
        session['participant_id'] = participant_id
        session['interaction_id'] = interaction_id
        session['concept_attempts'] = {}

        db_session_id = initialize_session_in_db()

        initialize_log_file(interaction_id, participant_id, trial_type)

        log_interaction("SYSTEM", None, f"Trial type changed from {old_trial_type} to {trial_type} for participant {participant_id}")
        log_interaction_to_db_only("SYSTEM", "Session", f"Trial type set to {trial_type} for participant {participant_id}")

        print(f"Successfully set trial type for participant {participant_id}")

        return jsonify({
            'status': 'success',
            'trial_type': trial_type,
            'interaction_id': interaction_id,
            'session_id': db_session_id  
        })
    except Exception as e:
        print(f"Error in set_trial_type: {str(e)}")
        return jsonify({
            "error": f"Server error: {str(e)}",
            "type": "server_error"
        }), 500

def log_user_interaction(interaction_type, details):
    """Log various types of user interactions with the system."""
    try:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        current_log_file = app.config.get('CURRENT_LOG_FILE')
        
        if not current_log_file:
            participant_id = session.get('participant_id')
            trial_type = session.get('trial_type')
            interaction_id = session.get('interaction_id', get_interaction_id())
            if participant_id and trial_type:
                initialize_log_file(interaction_id, participant_id, trial_type)
                current_log_file = app.config.get('CURRENT_LOG_FILE')
            else:
                print("Warning: Cannot log user interaction, participant ID or trial type not set.")
                return False

        if current_log_file: 
            with open(current_log_file, "a", encoding="utf-8") as file:
                file.write(f"[{timestamp}] SYSTEM: User {interaction_type}: {details}\n\n")
            
            print(f"User interaction logged: {interaction_type} - {details} in {current_log_file}")
            return True
        else:
            print("Error: No current log file path set to log user interaction.")
            return False
    except Exception as e:
        print(f"Error logging user interaction: {str(e)}")
        return False

@app.route('/log_interaction', methods=['POST'])
def log_user_interaction_endpoint():
    """Endpoint to log various user interactions."""
    data = request.get_json()
    interaction_type = data.get('type')
    details = data.get('details')
    
    if not interaction_type or not details:
        return jsonify({'error': 'Missing interaction type or details'}), 400
    
    log_user_interaction(interaction_type, details)
    return jsonify({'status': 'success'})

@app.route('/backup_to_cloud', methods=['POST'])
def backup_to_cloud():
    """Manual backup endpoint - no longer functional but kept for compatibility."""
    try:
        return jsonify({'status': 'info', 'message': 'Cloud backup functionality has been removed. Use /export_complete_data to download User Data.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# =========================== DATA EXPORT FUNCTIONALITY ===========================

@app.route('/data_dashboard')
def data_dashboard():
    """Display a simple dashboard for data export and management."""
    try:
        total_participants = Participant.query.count()
        total_sessions = Session.query.count()
        total_interactions = Interaction.query.count()
        total_recordings = Recording.query.count()
        
        recent_sessions = Session.query.order_by(Session.started_at.desc()).limit(10).all()
        
        stats = {
            'total_participants': total_participants,
            'total_sessions': total_sessions,
            'total_interactions': total_interactions,
            'total_recordings': total_recordings,
            'recent_sessions': [
                {
                    'session_id': s.session_id,
                    'participant_id': s.participant_id,
                    'trial_type': s.trial_type,
                    'version': s.version,
                    'started_at': s.started_at.strftime('%Y-%m-%d %H:%M:%S') if s.started_at else 'N/A'
                } for s in recent_sessions
            ]
        }
        
        return jsonify({
            'status': 'success',
            'message': 'HAI V2 Data Dashboard',
            'stats': stats
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/export_complete_data')
def export_complete_data():
    """Export available user data - files if they exist, otherwise comprehensive database export."""
    try:
        import zipfile
        import csv
        from io import StringIO, BytesIO
        from flask import make_response
        
        zip_buffer = BytesIO()
        files_found = False
        
        folders_to_export = [
            app.config.get('USER_DATA_BASE_FOLDER'),
            app.config.get('CONCEPT_AUDIO_FOLDER'),
            app.config.get('INTRO_AUDIO_FOLDER'),
        ]
        user_data_base = app.config.get('USER_DATA_BASE_FOLDER', '')
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for folder in folders_to_export:
                if folder and os.path.exists(folder):
                    print(f"Adding files from: {folder}")
                    for root, dirs, files in os.walk(folder):
                        for file in files:
                            file_path = os.path.join(root, file)
                            if folder == app.config['USER_DATA_BASE_FOLDER']:
                                rel_path = os.path.relpath(file_path, app.config['USER_DATA_BASE_FOLDER'])
                            else:
                                rel_path = os.path.relpath(file_path, app.config['UPLOAD_FOLDER'])
                            archive_path = f"Exported_Data/{rel_path}"
                            try:
                                zip_file.write(file_path, archive_path)
                                print(f"Added: {archive_path}")
                                files_found = True
                            except Exception as e:
                                print(f"Could not add file {file_path}: {str(e)}")
            log_path = os.path.join(app.config['UPLOAD_FOLDER'], 'conversation_log.txt')
            if os.path.exists(log_path):
                zip_file.write(log_path, 'Exported_Data/conversation_log.txt')
                files_found = True
            
            if not files_found:
                print("No user files found for export.")
                pass
        
        zip_buffer.seek(0)
        
        if zip_buffer.getvalue():
            filename_prefix = "HAI_V2_Files_Export" if files_found else "HAI_V2_Database_Export"
            response = make_response(zip_buffer.getvalue())
            response.headers['Content-Type'] = 'application/zip'
            response.headers['Content-Disposition'] = f'attachment; filename={filename_prefix}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'
            return response
        else:
            return jsonify({
                'status': 'error', 
                'message': 'No data available for export. Please ensure participants have completed interactions.'
            }), 404
            
    except Exception as e:
        print(f"Export error: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/diagnose_uploads')
def diagnose_uploads():
    """Return a JSON summary of upload folders and environment info for debugging (V2)."""
    try:
        def sample_files(base, limit=50):
            out = []
            if not base or not os.path.exists(base):
                return out
            for root, dirs, files in os.walk(base):
                for f in files:
                    path = os.path.join(root, f)
                    try:
                        out.append({'path': os.path.relpath(path, base), 'size': os.path.getsize(path), 'mtime': os.path.getmtime(path)})
                    except Exception:
                        out.append({'path': os.path.relpath(path, base), 'size': None, 'mtime': None})
                    if len(out) >= limit:
                        return out
            return out

        upload_folder = app.config.get('UPLOAD_FOLDER')
        user_data_base = app.config.get('USER_DATA_BASE_FOLDER')
        concept_audio_folder = app.config.get('CONCEPT_AUDIO_FOLDER')

        data = {
            'upload_folder': upload_folder,
            'upload_exists': os.path.exists(upload_folder) if upload_folder else False,
            'user_data_base': user_data_base,
            'user_data_exists': os.path.exists(user_data_base) if user_data_base else False,
            'concept_audio_folder': concept_audio_folder,
            'concept_audio_exists': os.path.exists(concept_audio_folder) if concept_audio_folder else False,
            'sample_upload_files': sample_files(upload_folder, limit=200),
            'sample_user_data_files': sample_files(user_data_base, limit=200),
            'openai_api_key_present': bool(os.environ.get('OPENAI_API_KEY')),
        }
        return jsonify({'status': 'ok', 'diagnostic': data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/export_latest_session')
def export_latest_session():
    """Export only the most recent session data for each participant."""
    try:
        import zipfile
        import csv
        from io import StringIO, BytesIO
        from flask import make_response
        
        zip_buffer = BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            
            latest_sessions = []
            participants = Participant.query.all()
            
            for participant in participants:
                latest_session = Session.query.filter_by(participant_id=participant.participant_id)\
                                                .order_by(Session.started_at.desc()).first()
                if latest_session:
                    latest_sessions.append(latest_session)
            
            if latest_sessions:
                csv_buffer = StringIO()
                writer = csv.writer(csv_buffer)
                writer.writerow([
                    'Participant_ID', 'Session_ID', 'Trial_Type', 'Version',
                    'Speaker', 'Concept_Name', 'Message', 'Attempt_Number', 
                    'Interaction_Time', 'Session_Started'
                ])
                
                for session in latest_sessions:
                    interactions = Interaction.query.filter_by(session_id=session.session_id)\
                                                      .order_by(Interaction.created_at.asc()).all()
                    
                    for interaction in interactions:
                        writer.writerow([
                            session.participant_id,
                            interaction.session_id,
                            session.trial_type,
                            session.version,
                            interaction.speaker,
                            interaction.concept_name,
                            interaction.message,
                            interaction.attempt_number,
                            interaction.created_at,
                            session.started_at
                        ])
                
                zip_file.writestr('Latest_Session_Interactions.csv', csv_buffer.getvalue())
                
                csv_buffer = StringIO()
                writer = csv.writer(csv_buffer)
                writer.writerow(['Participant_ID', 'Session_ID', 'Trial_Type', 'Version', 'Started_At', 'Total_Interactions'])
                
                for session in latest_sessions:
                    interactions_count = Interaction.query.filter_by(session_id=session.session_id).count()
                    writer.writerow([
                        session.participant_id,
                        session.session_id,
                        session.trial_type,
                        session.version,
                        session.started_at,
                        interactions_count
                    ])
                
                zip_file.writestr('Latest_Sessions_Summary.csv', csv_buffer.getvalue())
        
        zip_buffer.seek(0)
        
        if zip_buffer.getvalue():
            response = make_response(zip_buffer.getvalue())
            response.headers['Content-Type'] = 'application/zip'
            response.headers['Content-Disposition'] = f'attachment; filename=HAI_V2_Latest_Sessions_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'
            return response
        else:
            return jsonify({
                'status': 'error', 
                'message': 'No recent session data available for export.'
            }), 404
            
    except Exception as e:
        print(f"Latest session export error: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/browse_files')
def browse_files():
    """Browse local files in User Data folder."""
    try:
        user_data_path = app.config['USER_DATA_BASE_FOLDER']
        file_structure = {}
        
        if os.path.exists(user_data_path):
            for root, dirs, files in os.walk(user_data_path):
                rel_path = os.path.relpath(root, user_data_path)
                if rel_path == '.':
                    rel_path = 'root'
                
                file_structure[rel_path] = {
                    'directories': dirs,
                    'files': [
                        {
                            'name': f,
                            'size': os.path.getsize(os.path.join(root, f)),
                            'modified': datetime.fromtimestamp(os.path.getmtime(os.path.join(root, f))).strftime('%Y-%m-%d %H:%M:%S')
                        } for f in files
                    ]
                }
        
        return jsonify({
            'status': 'success',
            'file_structure': file_structure,
            'base_path': user_data_path
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# =========================== END DATA EXPORT FUNCTIONALITY ===========================

@app.route('/diagnostic_filesystem')
def diagnostic_filesystem():
    """Diagnostic route to check file system status on Render."""
    try:
        diagnostic_info = {
            'current_working_directory': os.getcwd(),
            'user_data_folder_exists': os.path.exists(app.config['USER_DATA_BASE_FOLDER']),
            'user_data_folder_path': app.config['USER_DATA_BASE_FOLDER'],
            'folder_contents': {},
            'disk_info': {},
            'environment_vars': {
                'PORT': os.environ.get('PORT', 'Not set'),
                'RENDER': os.environ.get('RENDER', 'Not set'),
                'DATABASE_URL': 'Set' if os.environ.get('DATABASE_URL') else 'Not set'
            }
        }
        
        user_data_path = app.config['USER_DATA_BASE_FOLDER']
        if os.path.exists(user_data_path):
            user_data_contents = []
            for root, dirs, files in os.walk(user_data_path):
                rel_path = os.path.relpath(root, user_data_path)
                user_data_contents.append({
                    'path': rel_path,
                    'directories': dirs,
                    'files': files,
                    'file_count': len(files)
                })
            diagnostic_info['folder_contents']['user_data'] = user_data_contents
        
        try:
            import shutil
            total, used, free = shutil.disk_usage('/')
            diagnostic_info['disk_info'] = {
                'total_gb': round(total / (1024**3), 2),
                'used_gb': round(used / (1024**3), 2),
                'free_gb': round(free / (1024**3), 2)
            }
        except:
            diagnostic_info['disk_info'] = 'Unable to get disk info'
            
        total_recordings = Recording.query.count()
        recordings_with_files = Recording.query.filter(Recording.file_path.isnot(None)).count()
        
        diagnostic_info['database_info'] = {
            'total_recordings_in_db': total_recordings,
            'recordings_with_file_paths': recordings_with_files,
            'sample_recording_paths': [r.file_path for r in Recording.query.limit(5).all() if r.file_path]
        }
        
        return jsonify({
            'status': 'success',
            'diagnostic': diagnostic_info
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'error_type': type(e).__name__
        }), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)