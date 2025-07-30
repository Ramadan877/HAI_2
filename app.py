#Version 2
from flask import Flask, request, render_template, jsonify, session, send_from_directory
from werkzeug.utils import secure_filename
import openai
import os.path
from gtts import gTTS
import whisper
# Handle pydub audioop compatibility for Python 3.13
try:
    from pydub import AudioSegment
except ImportError as e:
    # Fallback for missing audioop in Python 3.13
    import warnings
    warnings.filterwarnings("ignore")
    
    # Mock AudioSegment for basic functionality
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
import gc
import threading
from functools import lru_cache
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
import boto3
from dotenv import load_dotenv
from database import db, Participant, Session, Interaction, Recording, UserEvent
import uuid

load_dotenv()


def upload_to_s3(file_path, s3_key):
    """Upload file to S3 and return the URL."""
    try:
        if not s3_client or not BUCKET_NAME:
            print("S3 not configured, skipping upload")
            return None
            
        s3_client.upload_file(file_path, BUCKET_NAME, s3_key)
        return f"https://{BUCKET_NAME}.s3.{os.environ.get('AWS_REGION')}.amazonaws.com/{s3_key}"
    except Exception as e:
        print(f"Error uploading to S3: {str(e)}")
        return None

def save_interaction_to_db(session_id, speaker, concept_name, message, attempt_number=1):
    """Save interaction to database."""
    try:
        if not db or not app.config.get('SQLALCHEMY_DATABASE_URI'):
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
        if not db or not app.config.get('SQLALCHEMY_DATABASE_URI'):
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
        if not db or not app.config.get('SQLALCHEMY_DATABASE_URI'):
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
    """Save audio locally and backup to cloud storage."""
    try:
        local_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        if hasattr(audio_data, 'save'):
            audio_data.save(local_path)
        else:
            with open(local_path, 'wb') as f:
                f.write(audio_data)
        
        cloud_url = None
        try:
            s3_key = f"recordings/{session_id}/{filename}"
            cloud_url = upload_to_s3(local_path, s3_key)
        except Exception as s3_error:
            print(f"S3 upload failed, continuing without cloud backup: {str(s3_error)}")
        

        try:
            if cloud_url:
                file_size = os.path.getsize(local_path) if os.path.exists(local_path) else 0
                save_recording_to_db(
                    session_id=session_id,
                    recording_type=recording_type,
                    file_path=cloud_url,
                    original_filename=filename,
                    file_size=file_size,
                    concept_name=concept_name,
                    attempt_number=attempt_number
                )
        except Exception as db_error:
            print(f"Database recording save failed, continuing: {str(db_error)}")
        
        return local_path, cloud_url
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
    """Backup existing local files to cloud storage - can be called periodically."""
    try:
        participant_id = session.get('participant_id')
        trial_type = session.get('trial_type')
        session_id = session.get('session_id')
        
        if not all([participant_id, trial_type, session_id]):
            return False
            
        folders = get_participant_folder(participant_id, trial_type)
        participant_folder = folders['participant_folder']
        
        for filename in os.listdir(participant_folder):
            if filename.endswith(('.mp3', '.wav', '.webm')):
                local_path = os.path.join(participant_folder, filename)
                s3_key = f"recordings/{session_id}/{filename}"
                cloud_url = upload_to_s3(local_path, s3_key)
                
                if cloud_url:
                    recording_type = 'audio'
                    if 'user_' in filename:
                        recording_type = 'user_audio'
                    elif 'ai_' in filename:
                        recording_type = 'ai_audio'
                    elif 'screen_recording' in filename:
                        recording_type = 'screen'
                    
                    file_size = os.path.getsize(local_path)
                    save_recording_to_db(
                        session_id=session_id,
                        recording_type=recording_type,
                        file_path=cloud_url,
                        original_filename=filename,
                        file_size=file_size
                    )
        
        screen_folder = folders['screen_recordings_folder']
        if os.path.exists(screen_folder):
            for filename in os.listdir(screen_folder):
                if filename.endswith('.webm'):
                    local_path = os.path.join(screen_folder, filename)
                    s3_key = f"screen_recordings/{session_id}/{filename}"
                    cloud_url = upload_to_s3(local_path, s3_key)
                    
                    if cloud_url:
                        file_size = os.path.getsize(local_path)
                        save_recording_to_db(
                            session_id=session_id,
                            recording_type='screen',
                            file_path=cloud_url,
                            original_filename=filename,
                            file_size=file_size
                        )
        
        return True
    except Exception as e:
        print(f"Error backing up files to cloud: {str(e)}")
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
client = openai.OpenAI(
    api_key=OPENAI_API_KEY,
    base_url="https://api.openai.com/v1"
)

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
app.secret_key = os.environ.get('SECRET_KEY', 'fallback-secret-key')

db.init_app(app)

s3_client = boto3.client(
    's3',
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
    region_name=os.environ.get('AWS_REGION')
)

BUCKET_NAME = os.environ.get('CLOUD_STORAGE_BUCKET')

with app.app_context():
    db.create_all()

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
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
            return transcript.text
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
    """Generate a unique audio filename with participant ID and attempt number."""
    return f"{prefix}_{attempt_number}_{participant_id}{extension}"

def get_general_audio_filename(prefix, concept_name=None, extension='.mp3'):
    """Generate a filename for general audio (intro, concept intros)."""
    name_part = f"{prefix}_{secure_filename(concept_name)}" if concept_name else prefix
    return f"{name_part}{extension}"

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
    """Log when a user navigates to a different slide/concept."""
    data = request.get_json()
    slide_number = data.get('slide_number', 'unknown')
    concept_name = data.get('concept_name', 'unknown')
    
    current_time = datetime.now()
    
    if (last_concept_change['slide_number'] != slide_number or 
        last_concept_change['concept_name'] != concept_name or
        (last_concept_change['timestamp'] and 
         (current_time - last_concept_change['timestamp']).total_seconds() > 1)):
        
        message = f"User navigated to slide [{slide_number}] with the concept: [{concept_name}]"
        log_interaction("SYSTEM", concept_name, message)
        
        last_concept_change['slide_number'] = slide_number
        last_concept_change['concept_name'] = concept_name
        last_concept_change['timestamp'] = current_time
    
    return jsonify({'status': 'success', 'message': 'Navigation and concept change logged'})

change_concept.last_concept = None

executor = ThreadPoolExecutor(max_workers=4)


@app.route('/log_interaction_event', methods=['POST'])
def log_interaction_event():
    """Log user interaction events like chat window open/close, audio controls, etc."""
    data = request.get_json()
    event_type = data.get('event_type')
    event_details = data.get('details', {})
    concept_name = data.get('concept_name')
    
    message = ""
    if event_type == "CHAT_WINDOW":
        message = f"User {event_details.get('action', 'unknown')} the chat window"
    elif event_type == "PAGE_NAVIGATION":
        action = event_details.get('action', 'unknown')
        to_page = event_details.get('to_page', 'unknown')
        message = f"User navigated to slide [{to_page}] with the concept: [{concept_name}]"
    elif event_type == "AUDIO_PLAYBACK":
        message = f"User {event_details.get('action', 'unknown')} audio playback at {event_details.get('timestamp', '0')} seconds"
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
            message = f"User submitted recording (size: {blob_size} bytes, duration: {duration}) at {timestamp}"
    
    log_interaction("SYSTEM", concept_name, message)
    return jsonify({'status': 'success', 'message': 'Event logged successfully'})
    
def generate_audio_async(text, file_path):
    """Generate audio asynchronously"""
    return executor.submit(generate_audio, text, file_path)

def generate_audio(text, file_path):
    """Generate speech (audio) from the provided text using gTTS."""
    try:
        if len(text) > 500:
            chunks = [text[i:i+500] for i in range(0, len(text), 500)]
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
            
            combined.export(file_path, format="mp3")
            
            for temp in temp_files:
                try:
                    os.remove(temp)
                except:
                    pass
        else:
            tts = gTTS(text=text, lang='en')
            tts.save(file_path)
            
        if os.path.exists(file_path):
            print(f"Audio file successfully saved: {file_path}")
            return True
        else:
            print(f"Failed to save audio file: {file_path}")
            return False
    except Exception as e:
        print(f"Error generating audio: {str(e)}")
        return False


@app.route('/save_screen_recording', methods=['POST'])
def save_screen_recording():
    try:
        app.logger.info('Received save_screen_recording request')
        
        if 'screen_recording' not in request.files:
            app.logger.error('No screen recording file provided')
            return jsonify({'error': 'No screen recording file provided'}), 400
            
        screen_recording = request.files['screen_recording']
        trial_type = request.form.get('trial_type')
        participant_id = request.form.get('participant_id')
        
        app.logger.info(f'Received screen recording request - Participant: {participant_id}, Trial: {trial_type}')
        
        if not all([screen_recording, trial_type, participant_id]):
            app.logger.error('Missing required parameters')
            return jsonify({'error': 'Missing required parameters'}), 400
            
        task_folder = create_user_folders(participant_id, trial_type)
        app.logger.info(f'Task folder: {task_folder}')
        
        screen_recordings_dir = os.path.join(task_folder, 'Screen Recordings')
        app.logger.info(f'Screen recordings directory: {screen_recordings_dir}')
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'screen_recording_{timestamp}.webm'
        filepath = os.path.join(screen_recordings_dir, filename)
        
        app.logger.info(f'Saving screen recording to: {filepath}')
        
        screen_recording.save(filepath)
        
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            app.logger.info(f'Screen recording saved successfully: {filepath}')
            return jsonify({
                'status': 'success',
                'message': 'Screen recording saved successfully',
                'filepath': filepath
            })
        else:
            app.logger.error('Failed to save screen recording - file not created or empty')
            return jsonify({'error': 'Failed to save screen recording'}), 500
        
    except Exception as e:
        app.logger.error(f"Error saving screen recording: {str(e)}")
        return jsonify({'error': str(e)}), 500

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

    if audio_file:
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

    ai_response = generate_response(
        user_message,
        selected_concept["name"],
        selected_concept["golden_answer"],
        current_attempt_count + 1
    )

    if not ai_response:
        print("Error: AI response generation failed!")  
        return jsonify({'error': 'AI response generation failed.'})

    print(f"AI Response: {ai_response}") 

    log_interaction("AI", concept_name, ai_response)
    
    try:
        log_interaction_to_db_only("USER", concept_name, user_message, current_attempt_count + 1)
        log_interaction_to_db_only("AI", concept_name, ai_response, current_attempt_count + 1)
    except Exception as e:
        print(f"Database logging failed, but continuing: {str(e)}")

    task_folder = create_user_folders(participant_id, trial_type)
    ai_response_filename = get_audio_filename('AI', participant_id, trial_type, current_attempt_count + 1)
    audio_response_path = os.path.join(task_folder, ai_response_filename)
    generate_audio(ai_response, audio_response_path)
    
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
        print(f"Cloud backup failed, but continuing: {str(e)}")

    if not os.path.exists(audio_response_path):
        print("Error: AI audio file not created!")  
        return jsonify({'error': 'AI audio generation failed.'})

    trial_folder_map = {
        'Trial_1': 'main_task_1', 'Trial_2': 'main_task_2', 'Test': 'test_task'
    }
    trial_folder_name = trial_folder_map.get(trial_type, trial_type.lower())
    ai_audio_url = f"/uploads/UserData/{participant_id}/{trial_folder_name}/{ai_response_filename}"
    return jsonify({
        'response': ai_response,
        'ai_audio_url': ai_audio_url,
        'user_transcript': user_message 
    })

def generate_response(user_message, concept_name, golden_answer, attempt_count):
    """Generate a response dynamically using OpenAI GPT."""

    if not golden_answer or not concept_name:
        return "As your tutor, I'm not able to provide you with feedback without having context about your explanation. Please ensure the context is set."
    
    base_prompt = f"""
    Context: {concept_name}
    Golden Answer: {golden_answer}
    User Explanation: {user_message}
    
    You are a friendly and encouraging tutor, helping a student refine their understanding of a concept in a supportive way. Your goal is to evaluate the student's explanation of this concept and provide warm, engaging feedback:
        - If the user's explanation includes all the relevant aspects of the golden answer, celebrate their effort and reinforce their confidence. Inform them that their explanation is correct and they have completed the self-explanation for this concept. Instruct them to proceed to the next concept.
        - If the explanation is partially correct, acknowledge their progress and gently guide them toward refining their answer.
        - If it's incorrect, provide constructive and positive feedback without discouraging them. Offer hints and encouragement.
        - Do not provide the golden answer or parts of it directly. Instead, guide the user to arrive at it themselves.
    Use a conversational tone, making the user feel comfortable and motivated to keep trying but refrain from using emojis in the text.
    Ignore any emojis that are part of the user's explanation.
    If the user is not talking about the current concept, guide them back to the task of self-explaining the current concept.
    """

    user_prompt = f"""
    User Explanation: {user_message}
    """

    if attempt_count == 0:
        user_prompt += "\nIf the explanation is correct, communicate this to the user. If it is not correct, provide general feedback and a broad hint to guide the user."
    elif attempt_count == 1:
        user_prompt += "\nIf the explanation is correct, communicate this to the user. If it is not correct, provide more specific feedback and highlight key elements the user missed."
    elif attempt_count == 2:
        user_prompt += "\nIf the explanation is correct, communicate this to the user. If it is not correct, provide the correct explanation, as the user has made multiple attempts."
    else:
        user_prompt += "\nLet the user know they have completed three self-explanation attempts. Instruct them to stop here and tell them to continue with the next concept."

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": base_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=200,
            temperature=0.7,
        )

        ai_response = response.choices[0].message.content
        attempt_count += 1
        session['attempt_count'] = attempt_count
        return ai_response
    except Exception as e:
        return f"Error generating AI response: {str(e)}"

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
    """Manual backup of current session files to cloud storage."""
    try:
        success = backup_existing_files_to_cloud()
        if success:
            return jsonify({'status': 'success', 'message': 'Files backed up to cloud'})
        else:
            return jsonify({'status': 'error', 'message': 'Backup failed'}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)