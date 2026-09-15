import json
import os
import shutil
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_file,
    session,
)
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

UPLOAD_DIR = BASE_DIR / "uploads"
ORIGINAL_DIR = UPLOAD_DIR / "original"
RECEIVED_DIR = UPLOAD_DIR / "received"

DATA_DIR = BASE_DIR / "data"
REFERENCE_FILE = DATA_DIR / "references.json"


# ============================================================
# CONFIGURATION
# ============================================================

MAX_UPLOAD_SIZE = 25 * 1024 * 1024  # 25 MB

ALLOWED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
    ".webp",
}

ALLOWED_FORMATS = {
    "JPEG",
    "PNG",
    "BMP",
    "GIF",
    "TIFF",
    "WEBP",
}


# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "development-secret-change-me",
)

app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE


# ============================================================
# DIRECTORY SETUP
# ============================================================

def ensure_directories():
    """Create required directories and reference file."""
    directories = [
        ORIGINAL_DIR,
        RECEIVED_DIR,
        DATA_DIR,
    ]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    if not REFERENCE_FILE.exists():
        REFERENCE_FILE.write_text(
            "[]",
            encoding="utf-8",
        )


ensure_directories()


# ============================================================
# ERROR RESPONSE
# ============================================================

def json_error(message, status=400, details=None):
    """Return a consistent JSON error response."""
    payload = {
        "success": False,
        "error": message,
    }

    if details is not None:
        payload["details"] = details

    return jsonify(payload), status


# ============================================================
# TIME
# ============================================================

def now_iso():
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# FILE HELPERS
# ============================================================

def allowed_extension(filename):
    """Check file extension."""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def current_filename(role):
    """Get current filename from session."""
    key = f"{role}_filename"
    return session.get(key)


def current_path(role):
    """Return current stored image path."""
    filename = current_filename(role)

    if not filename:
        return None

    if role == "original":
        directory = ORIGINAL_DIR
    elif role == "received":
        directory = RECEIVED_DIR
    else:
        return None

    path = directory / filename

    if not path.exists() or not path.is_file():
        return None

    return path


def clear_role_file(role):
    """Delete all files for a specific role."""
    if role == "original":
        directory = ORIGINAL_DIR
    elif role == "received":
        directory = RECEIVED_DIR
    else:
        return

    if directory.exists():
        for file_path in directory.iterdir():
            if file_path.is_file():
                try:
                    file_path.unlink()
                except OSError:
                    pass

    session.pop(f"{role}_filename", None)


# ============================================================
# IMAGE VALIDATION
# ============================================================

def validate_image(path):
    """
    Validate that the supplied file is a real supported image.
    """
    if not path.exists():
        raise ValueError("Missing file.")

    if not path.is_file():
        raise ValueError("Invalid file.")

    if path.stat().st_size == 0:
        raise ValueError("Empty file.")

    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError(
            "Unsupported image format."
        )

    try:
        # First pass: verify file integrity
        with Image.open(path) as image:
            image.verify()

        # Second pass: identify format
        with Image.open(path) as image:
            image_format = image.format

            if image_format not in ALLOWED_FORMATS:
                raise ValueError(
                    "Unsupported image format."
                )

    except UnidentifiedImageError as exc:
        raise ValueError(
            "Unreadable image file."
        ) from exc

    except OSError as exc:
        raise ValueError(
            "Unreadable image file."
        ) from exc


def get_image(role):
    """Get and validate selected image."""
    path = current_path(role)

    if path is None:
        raise ValueError(
            f"No {role} image selected."
        )

    validate_image(path)

    return path


# ============================================================
# CRC-32C
# ============================================================

def crc32c(data):
    """
    CRC-32C / Castagnoli implementation.

    Polynomial:
        0x82F63B78

    Reflected algorithm.
    """
    crc = 0xFFFFFFFF

    for byte in data:
        crc ^= byte

        for _ in range(8):
            if crc & 1:
                crc = (
                    (crc >> 1)
                    ^ 0x82F63B78
                )
            else:
                crc >>= 1

    return (
        crc ^ 0xFFFFFFFF
    ) & 0xFFFFFFFF


# ============================================================
# CRC CALCULATION
# ============================================================

def calculate_crc(path, algorithm):
    """Calculate CRC using the requested algorithm."""
    algorithm = str(algorithm).upper()

    data = path.read_bytes()

    if algorithm == "CRC-32":
        return zlib.crc32(data) & 0xFFFFFFFF

    if algorithm == "CRC-32C":
        return crc32c(data)

    raise ValueError(
        "Unsupported CRC algorithm. "
        "Use CRC-32 or CRC-32C."
    )


# ============================================================
# REQUEST ALGORITHM
# ============================================================

def algorithm_from_request():
    """
    Safely extract CRC algorithm from JSON request.

    Works even when:
    - request has no JSON body
    - JSON body is empty
    - request content type is not JSON
    """
    body = request.get_json(silent=True)

    if not isinstance(body, dict):
        body = {}

    algorithm = body.get(
        "algorithm",
        "CRC-32",
    )

    if algorithm not in {
        "CRC-32",
        "CRC-32C",
    }:
        raise ValueError(
            "Algorithm must be CRC-32 or CRC-32C."
        )

    return algorithm


# ============================================================
# IMAGE INFORMATION
# ============================================================

def image_info(role, algorithm):
    """Return information about an image."""
    path = get_image(role)

    return {
        "role": role,
        "filename": path.name,
        "size": path.stat().st_size,
        "crc": f"{calculate_crc(path, algorithm):08X}",
        "algorithm": algorithm,
        "preview_url": (
            f"/api/preview/{role}/{path.name}"
        ),
    }


# ============================================================
# BYTE-BY-BYTE COMPARISON
# ============================================================

def compare_bytes(original_path, received_path):
    """
    Compare original and received images byte-by-byte.
    """
    original = original_path.read_bytes()
    received = received_path.read_bytes()

    maximum_length = max(
        len(original),
        len(received),
    )

    matching = 0
    first_mismatch = None

    for index in range(maximum_length):
        original_byte = (
            original[index]
            if index < len(original)
            else None
        )

        received_byte = (
            received[index]
            if index < len(received)
            else None
        )

        if original_byte == received_byte:
            matching += 1

        elif first_mismatch is None:
            first_mismatch = index

    different = (
        maximum_length - matching
    )

    similarity = (
        round(
            (matching / maximum_length) * 100,
            4,
        )
        if maximum_length
        else 100.0
    )

    return {
        "original_size": len(original),
        "received_size": len(received),
        "matching_bytes": matching,
        "different_bytes": different,
        "first_mismatch_position": (
            first_mismatch
        ),
        "similarity_percentage": similarity,
        "identical": original == received,
    }


# ============================================================
# CRC COMPARISON
# ============================================================

def crc_result(algorithm):
    """Compare CRC values of original and received."""
    original = get_image("original")
    received = get_image("received")

    original_crc = calculate_crc(
        original,
        algorithm,
    )

    received_crc = calculate_crc(
        received,
        algorithm,
    )

    return {
        "algorithm": algorithm,
        "original_crc": f"{original_crc:08X}",
        "received_crc": f"{received_crc:08X}",
        "match": original_crc == received_crc,
    }


# ============================================================
# REFERENCES
# ============================================================

def load_references():
    """Load saved reference records."""
    try:
        return json.loads(
            REFERENCE_FILE.read_text(
                encoding="utf-8"
            )
        )

    except (
        OSError,
        json.JSONDecodeError,
        TypeError,
    ):
        return []


def save_references(references):
    """Save reference records."""
    REFERENCE_FILE.write_text(
        json.dumps(
            references,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================
# REQUEST TOO LARGE
# ============================================================

@app.errorhandler(413)
def request_too_large(error):
    return json_error(
        "File is too large. Maximum size is 25 MB.",
        413,
    )


# ============================================================
# GLOBAL ERROR HANDLER
# ============================================================

@app.errorhandler(Exception)
def unexpected_error(error):
    app.logger.exception(
        "Unhandled server error"
    )

    return json_error(
        "Server error.",
        500,
    )


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():
    return render_template(
        "index.html"
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
def health():
    return jsonify({
        "success": True,
        "status": "ready",
        "service": (
            "Image Transfer Integrity Console"
        ),
    })


# ============================================================
# UPLOAD IMAGE
# ============================================================

@app.post("/api/upload/<role>")
def upload_image(role):

    if role not in {
        "original",
        "received",
    }:
        return json_error(
            "Invalid upload role."
        )

    if "image" not in request.files:
        return json_error(
            "No image selected."
        )

    uploaded = request.files["image"]

    if not uploaded or not uploaded.filename:
        return json_error(
            "No image selected."
        )

    original_name = secure_filename(
        uploaded.filename
    )

    if not original_name:
        return json_error(
            "Invalid filename."
        )

    if not allowed_extension(
        original_name
    ):
        return json_error(
            "Non-image file or unsupported "
            "image format. Supported formats: "
            "JPG, JPEG, PNG, BMP, GIF, TIFF, WEBP."
        )

    # Remove previous file
    clear_role_file(role)

    if role == "original":
        directory = ORIGINAL_DIR
    else:
        directory = RECEIVED_DIR

    stored_name = (
        f"{uuid.uuid4().hex}_"
        f"{original_name}"
    )

    destination = (
        directory / stored_name
    )

    try:
        uploaded.save(destination)

        validate_image(destination)

    except PermissionError:

        if destination.exists():
            destination.unlink(
                missing_ok=True
            )

        return json_error(
            "Permission error while saving "
            "the image.",
            500,
        )

    except ValueError as exc:

        destination.unlink(
            missing_ok=True
        )

        return json_error(
            str(exc)
        )

    except OSError:

        destination.unlink(
            missing_ok=True
        )

        return json_error(
            "Unable to read or save the image.",
            500,
        )

    session[
        f"{role}_filename"
    ] = stored_name

    session.modified = True

    # For multipart upload, algorithm comes
    # from form data rather than JSON.
    algorithm = request.form.get(
        "algorithm",
        "CRC-32",
    )

    if algorithm not in {
        "CRC-32",
        "CRC-32C",
    }:
        algorithm = "CRC-32"

    try:
        info = image_info(
            role,
            algorithm,
        )

    except Exception as exc:
        return json_error(
            str(exc)
        )

    return jsonify({
        "success": True,
        "event": (
            f"{role.title()} image selected"
        ),
        "image": info,
    })


# ============================================================
# SIMULATE TRANSFER
# ============================================================

@app.post("/api/transfer")
def simulate_transfer():

    try:
        original = get_image(
            "original"
        )

        # FIX:
        # Never directly call:
        # request.get_json(...).get(...)
        #
        # Instead safely obtain algorithm.
        algorithm = algorithm_from_request()

    except ValueError as exc:
        return json_error(
            str(exc)
        )

    # Remove old received image
    clear_role_file(
        "received"
    )

    destination = (
        RECEIVED_DIR
        / (
            f"transfer_"
            f"{uuid.uuid4().hex}_"
            f"{original.name}"
        )
    )

    try:
        # Copy binary data exactly.
        shutil.copyfile(
            original,
            destination,
        )

        validate_image(
            destination
        )

    except PermissionError:

        if destination.exists():
            destination.unlink(
                missing_ok=True
            )

        return json_error(
            "Permission error during "
            "transfer simulation.",
            500,
        )

    except ValueError as exc:

        if destination.exists():
            destination.unlink(
                missing_ok=True
            )

        return json_error(
            str(exc)
        )

    except OSError:

        if destination.exists():
            destination.unlink(
                missing_ok=True
            )

        return json_error(
            "Unable to simulate image transfer.",
            500,
        )

    session[
        "received_filename"
    ] = destination.name

    session.modified = True

    try:
        received_info = image_info(
            "received",
            algorithm,
        )

    except Exception as exc:
        return json_error(
            str(exc)
        )

    return jsonify({
        "success": True,
        "event": "Transfer simulated",
        "image": received_info,
    })


# ============================================================
# SIMULATE CORRUPTION
# ============================================================

@app.post("/api/corrupt")
def simulate_corruption():

    try:
        received = get_image(
            "received"
        )

    except ValueError as exc:
        return json_error(
            str(exc)
        )

    data = bytearray(
        received.read_bytes()
    )

    if not data:
        return json_error(
            "Received image is empty."
        )

    # Change middle byte
    position = len(data) // 2

    original_byte = data[position]

    changed_byte = (
        original_byte ^ 0x01
    )

    if changed_byte == original_byte:
        changed_byte = (
            original_byte + 1
        ) % 256

    data[position] = changed_byte

    try:
        received.write_bytes(
            data
        )

        # Do not allow a completely invalid
        # image to remain silently.
        validate_image(
            received
        )

    except PermissionError:

        return json_error(
            "Permission error during "
            "corruption simulation.",
            500,
        )

    except ValueError:

        # Restore original data if changing
        # the byte made the image unreadable.
        data[position] = original_byte

        try:
            received.write_bytes(
                data
            )
        except OSError:
            pass

        return json_error(
            "The selected byte could not be "
            "changed without making the image "
            "invalid. Try again."
        )

    except OSError:

        return json_error(
            "Unable to modify received image.",
            500,
        )

    return jsonify({
        "success": True,
        "event": "Corruption simulated",
        "changed_byte_position": position,
        "original_byte": (
            f"{original_byte:02X}"
        ),
        "changed_byte": (
            f"{changed_byte:02X}"
        ),
    })


# ============================================================
# CALCULATE CRC
# ============================================================

@app.post("/api/calculate-crc")
def calculate_crc_api():

    try:
        algorithm = algorithm_from_request()

    except ValueError as exc:
        return json_error(
            str(exc)
        )

    result = {
        "success": True,
        "algorithm": algorithm,
        "images": {},
    }

    for role in (
        "original",
        "received",
    ):
        if current_path(role):

            try:
                result["images"][role] = (
                    image_info(
                        role,
                        algorithm,
                    )
                )

            except ValueError:
                pass

    if not result["images"]:
        return json_error(
            "No image selected."
        )

    return jsonify(
        result
    )


# ============================================================
# VERIFY CRC
# ============================================================

@app.post("/api/verify-crc")
def verify_crc_api():

    try:
        algorithm = algorithm_from_request()
        result = crc_result(
            algorithm
        )

    except ValueError as exc:
        return json_error(
            str(exc)
        )

    return jsonify({
        "success": True,
        "event": "CRC checked",
        **result,
    })


# ============================================================
# VERIFY BYTES
# ============================================================

@app.post("/api/verify-bytes")
def verify_bytes_api():

    try:
        original = get_image(
            "original"
        )

        received = get_image(
            "received"
        )

    except ValueError as exc:
        return json_error(
            str(exc)
        )

    result = compare_bytes(
        original,
        received,
    )

    return jsonify({
        "success": True,
        "event": "Byte comparison completed",
        **result,
    })


# ============================================================
# FINAL VERIFY
# ============================================================

@app.post("/api/verify")
def verify_api():

    try:
        algorithm = algorithm_from_request()

        original = get_image(
            "original"
        )

        received = get_image(
            "received"
        )

        crc = crc_result(
            algorithm
        )

        byte_result = compare_bytes(
            original,
            received,
        )

    except ValueError as exc:
        return json_error(
            str(exc)
        )

    intact = (
        crc["match"]
        and byte_result["identical"]
    )

    return jsonify({

        "success": True,

        "event": "Final result",

        "crc": crc,

        "bytes": byte_result,

        "crc_status": (
            "MATCH"
            if crc["match"]
            else "MISMATCH"
        ),

        "byte_status": (
            "BYTE-FOR-BYTE IDENTICAL"
            if byte_result["identical"]
            else "DIFFERENT"
        ),

        "corruption_status": (
            "NO CORRUPTION DETECTED"
            if intact
            else "IMAGE CORRUPTION DETECTED"
        ),

        "overall_status": (
            "✓ IMAGE IS INTACT"
            if intact
            else "× IMAGE CORRUPTION DETECTED"
        ),

        "intact": intact,
    })


# ============================================================
# SAVE REFERENCE
# ============================================================

@app.post("/api/save-reference")
def save_reference():

    try:
        algorithm = algorithm_from_request()

        original = get_image(
            "original"
        )

    except ValueError as exc:
        return json_error(
            str(exc)
        )

    crc_value = calculate_crc(
        original,
        algorithm,
    )

    reference = {
        "reference_id": (
            uuid.uuid4().hex
        ),

        "filename": original.name,

        "size": original.stat().st_size,

        "algorithm": algorithm,

        "crc": f"{crc_value:08X}",

        "timestamp": now_iso(),
    }

    references = load_references()

    references.append(
        reference
    )

    try:
        save_references(
            references
        )

    except OSError:
        return json_error(
            "Unable to save reference.",
            500,
        )

    return jsonify({
        "success": True,
        "event": "Reference saved",
        "reference": reference,
    })


# ============================================================
# CHECK REFERENCE
# ============================================================

@app.post("/api/check-reference")
def check_reference():

    try:
        algorithm = algorithm_from_request()

        original = get_image(
            "original"
        )

    except ValueError as exc:
        return json_error(
            str(exc)
        )

    body = request.get_json(
        silent=True
    )

    if not isinstance(body, dict):
        body = {}

    reference_id = body.get(
        "reference_id"
    )

    references = load_references()

    if reference_id:

        matches = [
            item
            for item in references
            if item.get(
                "reference_id"
            ) == reference_id
        ]

    else:

        matches = [
            item
            for item in references
            if item.get(
                "algorithm"
            ) == algorithm
        ]

    if not matches:
        return json_error(
            "Missing reference."
        )

    reference = matches[-1]

    current_crc = (
        f"{calculate_crc(original, algorithm):08X}"
    )

    reference_matches = (
        reference.get(
            "algorithm"
        ) == algorithm
        and reference.get(
            "size"
        ) == original.stat().st_size
        and reference.get(
            "crc"
        ) == current_crc
    )

    return jsonify({
        "success": True,
        "event": "Checked against reference",
        "reference": reference,
        "current_crc": current_crc,
        "match": reference_matches,
    })


# ============================================================
# RESET
# ============================================================

@app.post("/api/reset")
def reset():

    clear_role_file(
        "original"
    )

    clear_role_file(
        "received"
    )

    session.clear()

    return jsonify({
        "success": True,
        "event": "Reset",
    })


# ============================================================
# IMAGE PREVIEW
# ============================================================

@app.get("/api/preview/<role>/<filename>")
def preview(role, filename):

    if role not in {
        "original",
        "received",
    }:
        return json_error(
            "Invalid image role.",
            404,
        )

    safe_filename = secure_filename(
        filename
    )

    if safe_filename != current_filename(
        role
    ):
        return json_error(
            "Image not found.",
            404,
        )

    path = current_path(
        role
    )

    if path is None:
        return json_error(
            "Image not found.",
            404,
        )

    return send_file(
        path
    )


# ============================================================
# START APPLICATION
# ============================================================

if __name__ == "__main__":

    ensure_directories()

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
    )