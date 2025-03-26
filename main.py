import datetime
import io
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

import pytz
from dotenv import load_dotenv
from fasthtml.common import *
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# === CONFIGURATION ===
load_dotenv()

# Load Google Service Account Credentials from environment variables
credentials = service_account.Credentials.from_service_account_info(
    {
        "type": os.getenv("GOOGLE_TYPE"),
        "project_id": os.getenv("GOOGLE_PROJECT_ID"),
        "private_key_id": os.getenv("GOOGLE_PRIVATE_KEY_ID"),
        "private_key": os.getenv("GOOGLE_PRIVATE_KEY").replace("\\n", "\n"),
        "client_email": os.getenv("GOOGLE_CLIENT_EMAIL"),
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "auth_uri": os.getenv("GOOGLE_AUTH_URI"),
        "token_uri": os.getenv("GOOGLE_TOKEN_URI"),
        "auth_provider_x509_cert_url": os.getenv("GOOGLE_AUTH_PROVIDER_X509_CERT_URL"),
        "client_x509_cert_url": os.getenv("GOOGLE_CLIENT_X509_CERT_URL"),
        "universe_domain": os.getenv("GOOGLE_UNIVERSE_DOMAIN"),
    },
    scopes=["https://www.googleapis.com/auth/drive"],
)

DRIVE_SERVICE = build("drive", "v3", credentials=credentials)

ADMIN_PASSWD = os.environ.get("ADMIN_PASSWD")
if not ADMIN_PASSWD:
    raise Exception("ADMIN_PASSWD environment variable not set.")

DRIVE_PARENT_FOLDER_ID = os.environ.get("DRIVE_PARENT_FOLDER_ID")
if not DRIVE_PARENT_FOLDER_ID:
    raise Exception("DRIVE_PARENT_FOLDER_ID environment variable not set.")


# === ENHANCED FOLDER BOOTSTRAPPING ===
# === APPLICATION INITIALIZATION ===


def verify_or_create_folder(parent_id, folder_name):
    """Verify if a folder exists, create it if not, and return its ID"""
    try:
        # Check if folder already exists (with shared drive support)
        query = f"'{parent_id}' in parents and name='{folder_name}' and mimeType='application/vnd.google-apps.folder'"
        results = (
            DRIVE_SERVICE.files()
            .list(
                q=query,
                fields="files(id,name)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )

        existing_folders = results.get("files", [])
        if existing_folders:
            return existing_folders[0]["id"]

        # Create the folder with shared drive support
        folder_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        folder = (
            DRIVE_SERVICE.files()
            .create(body=folder_metadata, fields="id", supportsAllDrives=True)
            .execute()
        )

        print(f"Created folder: {folder_name} (ID: {folder.get('id')})")
        return folder.get("id")
    except Exception as e:
        print(f"Error creating folder {folder_name}: {str(e)}")
        raise


def test_drive_access():
    """Test Drive access with more detailed verification"""
    try:
        # Test 1: Basic API access
        DRIVE_SERVICE.files().list(pageSize=1).execute()

        # Test 2: Try to find our parent folder
        try:
            folder = (
                DRIVE_SERVICE.files()
                .get(
                    fileId=DRIVE_PARENT_FOLDER_ID,
                    fields="id,name",
                    supportsAllDrives=True,
                )
                .execute()
            )
            print(f"Found folder: {folder.get('name')} (ID: {folder.get('id')})")
            return True
        except Exception as e:
            print(f"Could not access specific folder: {str(e)}")
            return False

    except Exception as e:
        print(f"Drive API access failed completely: {str(e)}")
        return False


def initialize_drive():
    """Comprehensive Drive initialization with error handling"""
    try:
        # First test basic access
        if not test_drive_access():
            raise Exception("Basic Drive access test failed")

        # Try to get parent folder with different approaches
        parent = None
        try:
            parent = (
                DRIVE_SERVICE.files()
                .get(
                    fileId=DRIVE_PARENT_FOLDER_ID,
                    fields="id,name,mimeType",
                    supportsAllDrives=True,
                )
                .execute()
            )
        except Exception as e:
            # Try without supportsAllDrives for My Drive folders
            try:
                parent = (
                    DRIVE_SERVICE.files()
                    .get(fileId=DRIVE_PARENT_FOLDER_ID, fields="id,name,mimeType")
                    .execute()
                )
            except Exception:
                raise Exception(f"Could not access folder with either method: {str(e)}")

        if not parent:
            raise Exception("Parent folder not found with any access method")

        if parent.get("mimeType") != "application/vnd.google-apps.folder":
            raise Exception(f"ID {DRIVE_PARENT_FOLDER_ID} is not a folder")

        print(f"Using parent folder: {parent.get('name')} (ID: {parent.get('id')})")

        # Now create semester folders
        semester_folders = {}
        for semester_num in range(1, 9):
            folder_name = f"Semester_{semester_num}"
            try:
                folder_id = verify_or_create_folder(DRIVE_PARENT_FOLDER_ID, folder_name)
                semester_folders[folder_name] = folder_id
            except Exception as e:
                print(f"Warning: Could not create {folder_name}: {str(e)}")
                continue

        return semester_folders

    except Exception as e:
        print(f"Drive initialization failed: {str(e)}")
        # Print troubleshooting info
        print("\nTROUBLESHOOTING INFO:")
        print(f"1. Parent Folder ID: {DRIVE_PARENT_FOLDER_ID}")
        print(f"2. Service Account: {credentials.service_account_email}")
        print("3. Make sure the service account has Editor access to the folder")
        if "shared drive" in str(e).lower():
            print(
                "4. For Shared Drives, ensure the service account is added as a member"
            )
        raise
        # === HELPER FUNCTIONS ===


def merge_zip_files(existing_path: Path, new_zip_path: Path):
    merged_temp = existing_path.parent / (existing_path.stem + "_merged.zip")
    with (
        zipfile.ZipFile(existing_path, "r") as old_zip,
        zipfile.ZipFile(new_zip_path, "r") as new_zip,
        zipfile.ZipFile(merged_temp, "w") as merged_zip,
    ):
        # Write files from the existing zip first
        for info in old_zip.infolist():
            merged_zip.writestr(info, old_zip.read(info.filename))
        # Add files from new zip (will replace duplicates)
        for info in new_zip.infolist():
            merged_zip.writestr(info, new_zip.read(info.filename))
    merged_temp.replace(existing_path)


def list_recent_uploads():
    """List recent uploads from Google Drive"""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
    all_files = []

    for semester, folder_id in SEMESTER_FOLDER_IDS.items():
        results = (
            DRIVE_SERVICE.files()
            .list(
                q=f"'{folder_id}' in parents and modifiedTime > '{cutoff.isoformat()}Z'",
                spaces="drive",
                fields="files(id, name, modifiedTime)",
            )
            .execute()
        )

        for file in results.get("files", []):
            all_files.append(
                {
                    "semester": semester,
                    "filename": file["name"],
                    "filepath": file["id"],
                    "modified": datetime.datetime.fromisoformat(
                        file.get("modifiedTime", "").replace("Z", "+00:00")
                    ),
                }
            )

    return all_files


def list_files_in_semester(semester: str):
    """List files for a specific semester in Google Drive"""
    semester_folder_id = SEMESTER_FOLDER_IDS.get(semester)
    if not semester_folder_id:
        return []

    query = f"'{semester_folder_id}' in parents"
    results = (
        DRIVE_SERVICE.files()
        .list(q=query, spaces="drive", fields="files(id, name)")
        .execute()
    )
    a = [
        {"filename": file["name"], "filepath": file["id"]}
        for file in results.get("files", [])
    ]
    return a[::-1]


def upload_file_to_drive(file_bytes, filename, semester):
    """Upload a file to Google Drive, using a temporary file for MediaFileUpload."""
    try:
        semester_folder_id = SEMESTER_FOLDER_IDS.get(semester)
        if not semester_folder_id:
            raise ValueError(f"Folder not found for semester: {semester}")

        print(f"Uploading {filename} to {semester} (Folder ID: {semester_folder_id})")

        # Create a BytesIO object
        file_stream = io.BytesIO(file_bytes)

        # Verify zip contents (and ensure stream is reset)
        try:
            with zipfile.ZipFile(file_stream) as zip_ref:
                bad_file = zip_ref.testzip()
                if bad_file:
                    raise zipfile.BadZipFile(
                        f"Corrupt file in ZIP: {bad_file}"
                    )  # Use BadZipFile Exception
                print(f"Zip verified: {len(zip_ref.namelist())} files")

            file_stream.seek(0)  # Reset to beginning *after* zipfile operations
        except zipfile.BadZipFile as e:
            raise ValueError(f"Invalid ZIP file: {str(e)}")  # Raise ValueError
        except Exception as e:
            raise ValueError(f"Error during ZIP verification: {str(e)}")

        # Create a temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
            tmp_file.write(file_stream.read())
            tmp_file_path = tmp_file.name

        # Create media upload
        media = MediaFileUpload(
            tmp_file_path, mimetype="application/zip", resumable=True
        )

        # Create file metadata
        file_metadata = {
            "name": filename,
            "parents": [semester_folder_id],
            "mimeType": "application/zip",
        }

        # Execute upload
        try:
            file = (
                DRIVE_SERVICE.files()
                .create(
                    body=file_metadata,
                    media_body=media,
                    fields="id",
                    supportsAllDrives=True,
                )
                .execute()
            )
        except Exception as e:
            print(f"Google Drive API Error: {str(e)}")
            raise  # Re-raise the exception to be caught outside
        finally:
            # Clean up the temporary file
            os.remove(tmp_file_path)

        print(f"Upload successful! File ID: {file.get('id')}")
        return file.get("id")

    except ValueError as ve:
        print(f"Validation Error: {str(ve)}")  # More specific message
        raise
    except Exception as e:
        print(f"General Upload Error: {str(e)}")
        raise


def download_file_from_drive(file_id):
    """Download a file from Google Drive"""
    request = DRIVE_SERVICE.files().get_media(fileId=file_id)
    file = io.BytesIO()
    downloader = MediaIoBaseDownload(file, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    file.seek(0)
    return file.read()


def merge_zip_files_in_drive(existing_file_id, new_file_bytes):
    """Merge two zip files in Google Drive"""
    # Download existing file
    existing_file_content = download_file_from_drive(existing_file_id)

    # Create temporary files for merging
    with (
        tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as existing_temp,
        tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as new_temp,
        tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as merged_temp,
    ):
        existing_temp.write(existing_file_content)
        new_temp.write(new_file_bytes)
        existing_temp.close()
        new_temp.close()

        # Merge zip files
        merge_zip_files(Path(existing_temp.name), Path(new_temp.name))
        shutil.copy(Path(existing_temp.name), Path(merged_temp.name))

    # Read merged file and upload
    with open(merged_temp.name, "rb") as f:
        merged_content = f.read()

    # Clean up temporary files
    os.unlink(existing_temp.name)
    os.unlink(new_temp.name)
    os.unlink(merged_temp.name)

    # Replace existing file in Drive
    media = MediaFileUpload(
        io.BytesIO(merged_content), resumable=True, mimetype="application/zip"
    )
    DRIVE_SERVICE.files().update(fileId=existing_file_id, media_body=media).execute()


def delete_file_in_drive(file_id):
    """Delete a file in Google Drive"""
    try:
        DRIVE_SERVICE.files().delete(fileId=file_id).execute()
        return True
    except Exception:
        return False


# === FASTHTML SETUP ===


def admin_auth_before(req, sess):
    if (
        req.url.path.startswith("/admin")
        and not req.url.path.startswith("/admin/login")
        and sess.get("admin") != True
    ):
        return RedirectResponse("/admin/login", status_code=303)


beforeware = Beforeware(admin_auth_before, skip=[r"/admin/login", r"/static/.*"])
app, rt = fast_app(before=beforeware, pico=True, live=False, debug=False)

# === ADMIN LOGIN ===


@rt("/admin/login", methods=["GET", "POST"])
async def admin_login(req, session):
    if req.method == "GET":
        return Titled(
            "Admin Login",
            Form(method="post")(
                Label("Password", Input(name="password", type="password")),
                Button("Login", type="submit"),
            ),
        )
    form = await req.form()
    if form.get("password") == ADMIN_PASSWD:
        session["admin"] = True
        return RedirectResponse("/admin")
    return Titled(
        "Admin Login", P("Incorrect password."), A("Back", href="/admin/login")
    )


# === ADMIN DASHBOARD ===


@rt("/admin")
def admin_dashboard(req, session):
    files = list_recent_uploads()
    return Titled(
        "Admin Dashboard",
        Ul(
            *[
                Li(
                    f"{f['semester']}/{f['filename']} (Uploaded at {f['modified']}) ",
                    A("Delete", href=f"/admin/delete?file={f['filepath']}"),
                )
                for f in files
            ]
        ),
        P(A("Upload New File", href="/admin/upload")),
    )


# === FILE UPLOAD AND CONFLICT RESOLUTION ===
def conflict_resolution_page(temp_path, existing_file_id, batch_year, semester):
    return Titled(
        "Conflict Resolution",
        P(
            f"A zip for batch {batch_year} already exists in {semester}. Choose resolution:"
        ),
        Form(method="post", action="/admin/upload/resolve")(
            Input(name="temp", type="hidden", value=str(temp_path)),
            Input(name="existing", type="hidden", value=existing_file_id),
            Label(
                "Resolution",
                Select(
                    Option("Remove & Replace", value="remove"),
                    Option("Merge", value="merge"),
                    name="action",
                ),
            ),
            Label(
                "Type REMOVE for confirmation 1",
                Input(name="confirm1", type="text"),
            ),
            Label(
                "Type REMOVE for confirmation 2",
                Input(name="confirm2", type="text"),
            ),
            Button("Resolve", type="submit"),
        ),
    )


@rt("/admin/upload", methods=["GET"])
async def admin_upload_form(req):
    """Handles the file upload form display."""
    semesters = list(SEMESTER_FOLDER_IDS.keys())
    current_year = datetime.datetime.now().year
    batch_years = list(range(current_year - 5, current_year + 1))  # Example range

    return Titled(
        "Admin Upload",
        Form(
            method="post",
            action="/admin/upload",
            enctype="multipart/form-data",
            _id="uploadForm",
        )(
            Label("File", Input(type="file", name="file", _id="fileInput")),
            Label(
                "Semester",
                Select(
                    *[Option(semester, value=semester) for semester in semesters],
                    name="semester",
                    _id="semesterSelect",
                ),
            ),
            Label(
                "Batch Year",
                Select(
                    *[
                        Option(year, value=year) for year in batch_years
                    ],  # Use batch_years
                    name="batch_year",
                    _id="batchYearSelect",
                ),
            ),
            Button("Upload", type="submit"),
        ),
    )


@rt("/admin/upload", methods=["POST"])
async def admin_upload(req):
    """Handles file uploads, including conflict resolution."""
    form = await req.form()
    file = form.get("file")
    semester = form.get("semester")
    batch_year = form.get("batch_year")

    if not file or not semester or not batch_year:
        return Titled("Error", P("Missing file or semester."))

    filename = file.filename
    file_bytes = await file.read()

    try:
        existing_files = list_files_in_semester(semester)
        existing_zip = next(
            (f for f in existing_files if f["filename"] == f"{batch_year}_batch.zip"),
            None,
        )

        if existing_zip:
            # Handle conflict
            temp_path = Path(tempfile.mkdtemp()) / filename
            with open(temp_path, "wb") as temp_file:
                temp_file.write(file_bytes)
            return conflict_resolution_page(
                temp_path, existing_zip["filepath"], batch_year, semester
            )
        else:
            # No conflict, upload directly
            try:
                upload_file_to_drive(file_bytes, f"{batch_year}_batch.zip", semester)
                return RedirectResponse("/admin", status_code=303)
            except Exception as e:
                return Titled("Upload Error", P(f"Error uploading file: {str(e)}"))

    except Exception as e:
        return Titled("Error", P(f"An error occurred: {str(e)}"))


@rt("/admin/upload/resolve", methods=["POST"])
async def admin_upload_resolve(req):
    """Handles the conflict resolution logic."""
    form = await req.form()
    temp_file_path = form.get("temp")
    existing_file_id = form.get("existing")
    action = form.get("action")
    confirm1 = form.get("confirm1")
    confirm2 = form.get("confirm2")

    if action == "remove" and confirm1 == "REMOVE" and confirm2 == "REMOVE":
        # Delete existing and replace
        try:
            file_bytes = Path(temp_file_path).read_bytes()
            semester = next(
                semester
                for semester, folder_id in SEMESTER_FOLDER_IDS.items()
                if folder_id
                in DRIVE_SERVICE.files()
                .get(fileId=existing_file_id, fields="parents", supportsAllDrives=True)
                .execute()
                .get("parents", [])
            )
            batch_year = Path(temp_file_path).stem.split("_")[0]  # Extract year
            delete_file_in_drive(existing_file_id)
            upload_file_to_drive(
                file_bytes, Path(temp_file_path).name, semester
            )  # Use original filename
            shutil.rmtree(Path(temp_file_path).parent)  # Clean up temp dir
            return RedirectResponse("/admin", status_code=303)
        except Exception as e:
            return Titled("Error", P(f"Error replacing file: {str(e)}"))

    elif action == "merge":
        # Merge the files
        try:
            file_bytes = Path(temp_file_path).read_bytes()
            merge_zip_files_in_drive(existing_file_id, file_bytes)
            shutil.rmtree(Path(temp_file_path).parent)  # Clean up temp dir
            return RedirectResponse("/admin", status_code=303)
        except Exception as e:
            return Titled("Error", P(f"Error merging files: {str(e)}"))

    else:
        return Titled(
            "Error", P("Invalid action or confirmation. Go back and try again.")
        )


# === FILE DELETION ===
@rt("/admin/delete")
def admin_delete(req, session):
    file_id = req.query.get("file")
    if not file_id:
        return Titled("Error", P("No file specified."))

    if delete_file_in_drive(file_id):
        return RedirectResponse("/admin", status_code=303)
    else:
        return Titled("Error", P("Failed to delete file."))


# === SEMESTER VIEW ===
@rt("/semester/{semester}")
def semester_view(req, semester):
    files = list_files_in_semester(semester)
    if not files:
        return Titled("Semester View", P("No files in this semester."))

    # Determine if any files are selected
    any_files_selected = len(files) > 0

    # Define the base URL for downloads
    base_url = f"/download/{semester}"

    # Generate unique IDs for the "Download All" link and the "Download Selected" button
    download_all_id = f"downloadAll-{semester}"
    download_selected_id = f"downloadSelected-{semester}"

    return Titled(
        f"Semester {semester}",
        Form(
            _id="fileListForm",
            hx_target="this",
            hx_on="htmx:after-request: if (event.detail.successful) { this.reset(); }",
        )(
            Ul(
                *[
                    Li(
                        Label(
                            Input(
                                type="checkbox",
                                name="selected_files",
                                value=file["filepath"],
                                _id=f"file-{file['filepath']}",
                            ),
                            file["filename"],
                        )
                    )
                    for file in files
                ]
            ),
            # "Download All" link using hx-post
            A(
                "Download All",
                href=base_url,
                hx_post=base_url,
                hx_trigger="click",
                hx_swap="none",
                _id=download_all_id,
            ),
            # "Download Selected" button, conditionally displayed
            Button(
                "Download Selected",
                hx_post=f"{base_url}/selected",
                hx_include="#fileListForm",
                hx_trigger="click",
                hx_swap="none",
                _id=download_selected_id,
                style="display:none" if not any_files_selected else "",
            ),
            Script(
                """
                document.addEventListener('DOMContentLoaded', function() {
                    const form = document.getElementById('fileListForm');
                    const downloadSelectedButton = document.getElementById('"""
                + download_selected_id
                + """');

                    form.addEventListener('change', function() {
                        const checkboxes = document.querySelectorAll('input[name="selected_files"]:checked');
                        if (checkboxes.length > 0) {
                            downloadSelectedButton.style.display = 'inline-block';
                        } else {
                            downloadSelectedButton.style.display = 'none';
                        }
                    });
                });
                """
            ),
        ),
    )


# === FILE DOWNLOAD ===
@rt("/download/{semester}", methods=["POST", "GET"])
async def download_all(req, semester):
    """Zips and sends all files for a semester."""
    files = list_files_in_semester(semester)
    if not files:
        return Response("No files to download", status_code=404)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file in files:
            try:
                file_content = download_file_from_drive(file["filepath"])
                zip_file.writestr(file["filename"], file_content)
            except Exception as e:
                print(f"Error downloading {file['filename']}: {e}")
                return Response(
                    f"Error downloading {file['filename']}", status_code=500
                )

    zip_buffer.seek(0)
    headers = {
        "Content-Type": "application/zip",
        "Content-Disposition": f'attachment; filename="{semester}_files.zip"',
    }
    return Response(zip_buffer.read(), headers=headers)


@rt("/download/{semester}/selected", methods=["POST"])
async def download_selected(req, semester):
    """Zips and sends selected files for a semester."""
    form = await req.form()
    selected_files = form.getall("selected_files")

    if not selected_files:
        return Response("No files selected", status_code=400)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_id in selected_files:
            try:
                file_info = next(
                    (
                        f
                        for f in list_files_in_semester(semester)
                        if f["filepath"] == file_id
                    ),
                    None,
                )
                if file_info:
                    file_content = download_file_from_drive(file_id)
                    zip_file.writestr(file_info["filename"], file_content)
                else:
                    print(f"File with ID {file_id} not found in semester {semester}")
                    return Response(
                        f"File with ID {file_id} not found", status_code=404
                    )
            except Exception as e:
                print(f"Error downloading file {file_id}: {e}")
                return Response(f"Error downloading file {file_id}", status_code=500)

    zip_buffer.seek(0)
    headers = {
        "Content-Type": "application/zip",
        "Content-Disposition": f'attachment; filename="selected_files.zip"',
    }
    return Response(zip_buffer.read(), headers=headers)


# === STATIC FILES ===
app.add_static_route("/static", Path("./static"))

# === START THE SERVER ===
if __name__ == "__main__":
    SEMESTER_FOLDER_IDS = initialize_drive()
    app.run(port=8000)
