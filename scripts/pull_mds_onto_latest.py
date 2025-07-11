import requests
from bs4 import BeautifulSoup
import os
from github import Github # pip install PyGithub
import base64
import re
from packaging.version import parse as parse_version, InvalidVersion # pip install packaging

# --- Configuration ---
BITBUCKET_DIR_URL = "https://cwrusdle.bitbucket.io/files/"
GITHUB_REPO_OWNER = "ucf-photovoltaics"  # <<< IMPORTANT: REPLACE WITH YOUR GITHUB USERNAME OR ORGANIZATION
GITHUB_REPO_NAME = "pv-ontology"       # <<< IMPORTANT: REPLACE WITH YOUR GITHUB REPOSITORY NAME
TARGET_REPO_DIR = "ontology"           # Directory in your GitHub repo to save files (e.g., "ontology" or "data")
GITHUB_TOKEN = os.getenv("GH_PAT")
TARGET_BRANCH = "main" # Or "master" or your specific target branch

def get_file_list_from_html(url):
    """Fetches the HTML directory listing and extracts file names."""
    print(f"Fetching directory listing from: {url}")
    try:
        response = requests.get(url)
        response.raise_for_status() # Raise an exception for HTTP errors
        soup = BeautifulSoup(response.text, 'html.parser')

        file_names = []
        # Look for links that point to files, typically not ending with '/'
        for link in soup.find_all('a'):
            href = link.get('href')
            text = link.get_text()

            # Basic filtering:
            # 1. Skip parent directory link (..)
            # 2. Skip directory links (usually end with '/')
            # 3. Ensure href is not empty and matches the link text (common for files in simple listings)
            if href and href != "../" and not href.endswith('/') and href == text:
                file_names.append(href)
        return file_names
    except requests.exceptions.RequestException as e:
        print(f"Error fetching directory listing: {e}")
        return []

def download_file(url, local_path):
    """Downloads a single file."""
    print(f"Downloading {url} to {local_path}")
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"Downloaded {local_path}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error downloading {url}: {e}")
        return False

def main():
    if not GITHUB_TOKEN:
        print("Error: GitHub PAT not found in environment variable 'GH_PAT'.")
        exit(1)

    # Initialize GitHub API
    g = Github(GITHUB_TOKEN)
    try:
        repo = g.get_user().get_repo(GITHUB_REPO_NAME) # Access repo via user, not organization directly for simple cases
    except Exception as e:
        print(f"Error accessing GitHub repository '{GITHUB_REPO_NAME}': {e}")
        print("Please check GITHUB_REPO_OWNER, GITHUB_REPO_NAME, and GH_PAT permissions.")
        exit(1)

    # Get list of ALL files from Bitbucket directory
    all_bitbucket_files = get_file_list_from_html(BITBUCKET_DIR_URL)
    if not all_bitbucket_files:
        print("No files found or error fetching from Bitbucket directory. Exiting.")
        return

    # --- Find the most recent version of MDS_Onto file ---
    # Define your pattern for the MDS_Onto files. This regex captures the version number.
    mds_onto_pattern = re.compile(r'MDS_Onto-v(\d+\.\d+\.\d+\.\d+)\.jsonld')

    found_versions = {} # Store {parsed_version_object: filename_string}

    for filename in all_bitbucket_files:
        match = mds_onto_pattern.match(filename)
        if match:
            version_str = match.group(1)
            try:
                # Use packaging.version to correctly compare semantic versions
                parsed_version = parse_version(version_str)
                found_versions[parsed_version] = filename
            except InvalidVersion:
                print(f"Warning: Could not parse version '{version_str}' from '{filename}'. Skipping.")
                continue

    if not found_versions:
        print("No files matching 'MDS_Onto-v*.jsonld' pattern found. Exiting.")
        return

    # Get the highest version's filename
    latest_parsed_version = max(found_versions.keys())
    latest_filename = found_versions[latest_parsed_version]

    print(f"Identified latest MDS_Onto file: {latest_filename} (Version: {latest_parsed_version})")

    # Create a temporary directory for downloads
    temp_download_dir = "temp_downloads"
    os.makedirs(temp_download_dir, exist_ok=True)

    # Define paths for the latest file
    bitbucket_file_url = f"{BITBUCKET_DIR_URL}{latest_filename}"
    local_file_path = os.path.join(temp_download_dir, latest_filename)
    github_file_path = os.path.join(TARGET_REPO_DIR, latest_filename).replace("\\", "/") # Ensure forward slashes for GitHub API

    # --- Download and update/create the latest file ---
    if not download_file(bitbucket_file_url, local_file_path):
        print(f"Failed to download the latest file: {latest_filename}. Exiting.")
        # Clean up temporary downloads before exiting on failure
        import shutil
        if os.path.exists(temp_download_dir):
            shutil.rmtree(temp_download_dir)
        exit(1)

    with open(local_file_path, 'rb') as f:
        new_content_bytes = f.read()

    try:
        # Try to get the existing file content and SHA
        existing_file = repo.get_contents(github_file_path, ref=TARGET_BRANCH)
        existing_content_bytes = base64.b64decode(existing_file.content)

        if new_content_bytes == existing_content_bytes:
            print(f"'{latest_filename}' content has not changed. Skipping update.")
        else:
            print(f"'{latest_filename}' content has changed. Updating in GitHub.")
            repo.update_file(
                path=github_file_path,
                message=f"Automated: Update {latest_filename} (v{latest_parsed_version}) from Bitbucket",
                content=new_content_bytes,
                sha=existing_file.sha, # Required for update
                branch=TARGET_BRANCH
            )
            print(f"'{latest_filename}' updated successfully.")
    except Exception as e:
        # If file doesn't exist, create it
        if "Not Found" in str(e):
            print(f"'{latest_filename}' not found in GitHub repo. Creating new file.")
            repo.create_file(
                path=github_file_path,
                message=f"Automated: Add {latest_filename} (v{latest_parsed_version}) from Bitbucket",
                content=new_content_bytes,
                branch=TARGET_BRANCH
            )
            print(f"'{latest_filename}' created successfully.")
        else:
            print(f"An unexpected error occurred with GitHub API for '{latest_filename}': {e}")
            # Clean up temporary downloads before exiting on failure
            import shutil
            if os.path.exists(temp_download_dir):
                shutil.rmtree(temp_download_dir)
            exit(1)

    # --- Cleanup: Delete older versions of the ontology file ---
    print("Checking for older versions of MDS-Onto to remove from GitHub...")
    try:
        contents = repo.get_contents(TARGET_REPO_DIR, ref=TARGET_BRANCH)
        if not isinstance(contents, list): # If it's a single file, it's not a directory or is empty
             print(f"'{TARGET_REPO_DIR}' is not a directory or is empty for cleanup.")
             contents = [] # Treat as empty for the loop below
    except Exception as e:
        print(f"Could not retrieve contents of '{TARGET_REPO_DIR}' for cleanup: {e}")
        contents = []

    for item in contents:
        if item.type == "file":
            github_file_name = os.path.basename(item.path)
            # Check if this file is an MDS_Onto file AND not the latest one
            if mds_onto_pattern.match(github_file_name) and github_file_name != latest_filename:
                print(f"Found older version '{github_file_name}'. Deleting from GitHub.")
                try:
                    repo.delete_file(
                        path=item.path,
                        message=f"Automated: Delete old version of MDS-Onto ({github_file_name})",
                        sha=item.sha, # Required for delete
                        branch=TARGET_BRANCH
                    )
                    print(f"'{github_file_name}' deleted successfully.")
                except Exception as e:
                    print(f"Error deleting '{github_file_name}': {e}")
            else:
                print(f"Keeping '{github_file_name}'.")

    # Clean up temporary downloads directory
    import shutil
    if os.path.exists(temp_download_dir):
        shutil.rmtree(temp_download_dir)
        print(f"Cleaned up temporary directory: {temp_download_dir}")

if __name__ == "__main__":
    main()
