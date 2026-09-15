# Image Transfer Integrity Console

A Flask-based image corruption detection dashboard.

The application supports:

- JPG and JPEG
- PNG
- BMP
- GIF
- TIFF
- WEBP
- CRC-32 using Python `zlib.crc32()`
- CRC-32C using a correct Castagnoli implementation
- Python byte-by-byte comparison
- Exact binary transfer simulation
- One-byte corruption simulation
- Reference CRC storage
- Activity logging
- JSON API error responses
- Responsive HTML/CSS/JavaScript frontend

## Requirements

- Python 3.9 or newer
- pip

## Installation

From the project directory:

```bash
pip install -r requirements.txt