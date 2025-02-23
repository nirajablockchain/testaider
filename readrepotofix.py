import subprocess
import os
import sys
import re
import glob
from typing import Optional, List, Set, Tuple
from collections import Counter

# Configuration
MAX_ITERATIONS = 10
PROJECT_DIR = "."  # Root of the Maven project
TARGET_SUBDIR = "src/main/java"  # Code subdirectory for fixes
KNOWLEDGE_DIR = "knowledge_repo"  # Directory with Markdown files
LOG_FILE = "build_log.txt"
ERROR_LOG = "error_log.txt"
AIDER_CMD = ["aider", "--script", "--no-auto-commit", "--yes", "--map-tokens", "1024"]
MAX_ERROR_LINES = 50
MAX_KNOWLEDGE_LINES = 100  # Limit trimmed knowledge content

def get_subdir_files(subdir: str) -> List[str]:
    """Get all .java files and pom.xml within the subdirectory."""
    files: Set[str] = set()
    pom_path = os.path.join(PROJECT_DIR, "pom.xml")
    if os.path.exists(pom_path):
        files.add(pom_path)
    
    subdir_path = os.path.join(PROJECT_DIR, subdir)
    java_files = glob.glob(os.path.join(subdir_path, "**/*.java"), recursive=True)
    files.update(java_files)
    
    if not java_files:
        print(f"Warning: No .java files found in {subdir_path}. Including pom.xml only.")
    
    return list(files)

def extract_error_keywords(error_text: str) -> Set[str]:
    """Extract key terms from the error text for relevancy scoring."""
    keywords = set()
    
    # Extract package names, symbols, and common terms
    keywords.update(re.findall(r"package (\S+) does not exist", error_text))
    keywords.update(re.findall(r"cannot find symbol.*?symbol:.*?(\S+)", error_text, re.DOTALL))
    keywords.update(re.findall(r"(\S+\.java)", error_text))
    keywords.add("pom.xml")  # Always relevant for dependencies
    
    return keywords

def trim_knowledge_repo(knowledge_files: List[str], error_keywords: Set[str]) -> str:
    """Trim the knowledge repo to relevant blocks based on error keywords."""
    relevant_blocks = []
    
    for file in knowledge_files:
        with open(file, "r") as f:
            content = f.read()
        
        # Split into blocks (sections between headers or blank lines)
        blocks = re.split(r"\n#{1,6}\s+|\n\n", content)
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            
            # Score block by keyword matches
            score = sum(1 for keyword in error_keywords if keyword.lower() in block.lower())
            if score > 0:
                relevant_blocks.append((score, block))
    
    # Sort by score (descending) and trim to MAX_KNOWLEDGE_LINES
    relevant_blocks.sort(reverse=True)
    trimmed_content = []
    total_lines = 0
    
    for _, block in relevant_blocks:
        block_lines = block.splitlines()
        if total_lines + len(block_lines) <= MAX_KNOWLEDGE_LINES:
            trimmed_content.append(block)
            total_lines += len(block_lines)
        else:
            remaining_lines = MAX_KNOWLEDGE_LINES - total_lines
            trimmed_content.append("\n".join(block_lines[:remaining_lines]))
            break
    
    result = "\n\n".join(trimmed_content)
    if result and total_lines > MAX_KNOWLEDGE_LINES:
        result += "\n[Note: Knowledge repo trimmed to most relevant blocks.]"
    
    return result if result else "No relevant knowledge found."

def get_knowledge_files(knowledge_dir: str) -> List[str]:
    """Get all Markdown files in the knowledge directory."""
    knowledge_path = os.path.join(PROJECT_DIR, knowledge_dir)
    md_files = glob.glob(os.path.join(knowledge_path, "**/*.md"), recursive=True)
    
    if not md_files:
        print(f"Warning: No Markdown files found in {knowledge_path}. Proceeding without knowledge repo.")
    
    return md_files

def run_maven_via_aider(subdir_files: List[str], knowledge_files: List[str]) -> tuple[bool, bool]:
    """Use Aider to run Maven compile and test."""
    compile_cmd = f"/run mvn clean compile -f {os.path.join(PROJECT_DIR, 'pom.xml')} > {LOG_FILE} 2>&1"
    test_cmd = f"/run mvn test -f {os.path.join(PROJECT_DIR, 'pom.xml')} >> {LOG_FILE} 2>&1"
    
    all_files = subdir_files + knowledge_files
    compile_result = subprocess.run(
        AIDER_CMD + ["--message", compile_cmd] + all_files,
        text=True,
        capture_output=True
    )
    
    compile_success = compile_result.returncode == 0 and "BUILD SUCCESS" in compile_result.stdout
    test_success = False
    
    if compile_success:
        test_result = subprocess.run(
            AIDER_CMD + ["--message", test_cmd] + all_files,
            text=True,
            capture_output=True
        )
        test_success = test_result.returncode == 0 and "BUILD SUCCESS" in test_result.stdout
    
    with open(LOG_FILE, "w") as log:
        log.write(compile_result.stdout)
        if compile_success:
            log.write(test_result.stdout)
    
    return compile_success, test_success

def extract_and_trim_errors() -> Optional[str]:
    """Extract and trim error lines from the build log."""
    if not os.path.exists(LOG_FILE):
        return None
    
    with open(LOG_FILE, "r") as log:
        lines = log.readlines()
    
    error_lines = []
    seen_errors: Set[str] = set()
    
    for i, line in enumerate(lines):
        if "[ERROR]" in line:
            error_block = "".join(lines[i:i+6])
            error_signature = re.sub(r":\d+:", ":<line>:", line)
            if error_signature not in seen_errors:
                seen_errors.add(error_signature)
                error_lines.append(error_block)
                if len(error_lines) * 6 >= MAX_ERROR_LINES:
                    break
    
    with open(ERROR_LOG, "w") as err_log:
        err_log.writelines(error_lines)
    
    if not error_lines:
        return None
    
    trimmed = "".join(error_lines)
    if len(lines) > MAX_ERROR_LINES and trimmed:
        trimmed += "\n[Note: Error log truncated. Showing first unique errors only.]"
    return trimmed

def get_relevant_files_from_error(error_text: str, subdir_files: List[str]) -> List[str]:
    """Use grep to find relevant files within the subdirectory."""
    files: Set[str] = set()
    pom_path = os.path.join(PROJECT_DIR, "pom.xml")
    if os.path.exists(pom_path):
        files.add(pom_path)

    file_matches = re.findall(r"(\S+\.java):(?:\d+):", error_text)
    error_files = {os.path.join(PROJECT_DIR, f.strip("/")) for f in file_matches}
    relevant_files = {f for f in subdir_files if f in error_files}
    files.update(relevant_files)
    
    if not relevant_files:
        print("Warning: No specific .java files in subdirectory matched. Using pom.xml and knowledge repo.")
    
    return list(files)

def run_aider_fix(error_text: str, subdir_files: List[str], knowledge_files: List[str]) -> bool:
    """Run Aider to fix the error using the trimmed knowledge repo."""
    files_to_fix = get_relevant_files_from_error(error_text, subdir_files)
    if not files_to_fix:
        print("No files to fix. Cannot proceed with Aider.")
        return False
    
    error_keywords = extract_error_keywords(error_text)
    trimmed_knowledge = trim_knowledge_repo(knowledge_files, error_keywords)
    
    all_files = files_to_fix + knowledge_files
    message = (
        f"Fix this Maven compilation or test error within {TARGET_SUBDIR}:\n\n"
        f"{error_text}\n\n"
        "Use this trimmed knowledge from the repo to inform fixes:\n"
        f"{trimmed_knowledge}\n\n"
        "Modify the code files (e.g., pom.xml, Java source) to resolve the issue. "
        "The repo map may also help identify dependencies or related code."
    )
    
    cmd = AIDER_CMD + ["--message", message] + all_files
    try:
        result = subprocess.run(cmd, text=True, capture_output=True)
        print(f"Aider output:\n{result.stdout}")
        if result.stderr:
            print(f"Aider errors:\n{result.stderr}")
        return result.returncode == 0
    except subprocess.SubprocessError as e:
        print(f"Error running Aider: {e}")
        return False

def main():
    print(f"Starting Aider-driven agent with trimmed knowledge repo from {KNOWLEDGE_DIR}...")
    subdir_files = get_subdir_files(TARGET_SUBDIR)
    knowledge_files = get_knowledge_files(KNOWLEDGE_DIR)
    print(f"Code files in scope: {subdir_files}")
    print(f"Knowledge files: {knowledge_files}")
    
    for i in range(1, MAX_ITERATIONS + 1):
        print(f"Iteration {i}: Compiling and testing via Aider...")
        compile_success, test_success = run_maven_via_aider(subdir_files, knowledge_files)
        
        if compile_success and test_success:
            print(f"Build and tests succeeded after {i} iterations!")
            os.remove(LOG_FILE)
            os.remove(ERROR_LOG) if os.path.exists(ERROR_LOG) else None
            sys.exit(0)
        
        error_text = extract_and_trim_errors()
        if not error_text:
            print("No errors found, but build or tests failed. Check logs manually.")
            break
        
        print(f"Iteration {i}: Trimmed errors:\n{error_text}")
        print(f"Iteration {i}: Running Aider to fix with trimmed knowledge repo...")
        if not run_aider_fix(error_text, subdir_files, knowledge_files):
            print(f"Iteration {i}: Aider failed to apply a fix. Continuing to next iteration.")
        
        import time
        time.sleep(1)
    
    print(f"Maximum iterations ({MAX_ITERATIONS}) reached. Could not resolve all issues.")
    print(f"Check {LOG_FILE} and {ERROR_LOG} for details.")
    sys.exit(1)

if __name__ == "__main__":
    main()
