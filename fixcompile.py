import subprocess
import os
import sys
import re
import glob
from typing import Optional, List, Set
import javalang

# Configuration
MAX_ITERATIONS = 10
PROJECT_DIR = "."  # Root of the Maven project
TARGET_SUBDIR = "src/main/java"  # Subdirectory for Aider to focus on
LOG_FILE = "build_log.txt"
ERROR_LOG = "error_log.txt"
AIDER_CMD = ["aider", "--script", "--no-auto-commit", "--yes", "--map-tokens", "1024"]
MAX_ERROR_LINES = 50

def parse_java_file(file_path: str) -> Optional[javalang.ast.Node]:
    """Parse a Java file into an AST."""
    try:
        with open(file_path, "r") as f:
            source = f.read()
        return javalang.parse.parse(source)
    except (javalang.parser.JavaSyntaxError, FileNotFoundError) as e:
        print(f"Failed to parse {file_path}: {e}")
        return None

def get_subdir_files(subdir: str) -> List[str]:
    """Get all .java files and pom.xml within the subdirectory and its subdirectories."""
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

def run_maven_via_aider(subdir_files: List[str]) -> tuple[bool, bool]:
    """Use Aider to run Maven compile and test, return (compile_success, test_success)."""
    compile_cmd = f"/run mvn clean compile -f {os.path.join(PROJECT_DIR, 'pom.xml')} > {LOG_FILE} 2>&1"
    test_cmd = f"/run mvn test -f {os.path.join(PROJECT_DIR, 'pom.xml')} >> {LOG_FILE} 2>&1"
    
    compile_result = subprocess.run(
        AIDER_CMD + ["--message", compile_cmd] + subdir_files,
        text=True,
        capture_output=True
    )
    
    compile_success = compile_result.returncode == 0 and "BUILD SUCCESS" in compile_result.stdout
    test_success = False
    
    if compile_success:
        test_result = subprocess.run(
            AIDER_CMD + ["--message", test_cmd] + subdir_files,
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
    """Use grep and ASTs to find relevant files within the subdirectory."""
    files: Set[str] = set()
    pom_path = os.path.join(PROJECT_DIR, "pom.xml")
    if os.path.exists(pom_path):
        files.add(pom_path)

    # Grep: Extract file paths from error text
    file_matches = re.findall(r"(\S+\.java):(?:\d+):", error_text)
    error_files = {os.path.join(PROJECT_DIR, f.strip("/")) for f in file_matches}
    candidate_files = {f for f in subdir_files if f in error_files}

    # AST: Refine with semantic analysis
    for java_file in candidate_files:
        ast = parse_java_file(java_file)
        if not ast:
            continue
        
        # Check imports
        for _, node in ast.filter(javalang.tree.Import):
            if node.path in error_text:
                files.add(java_file)
                break
        
        # Check class declarations
        for _, node in ast.filter(javalang.tree.ClassDeclaration):
            if node.name in error_text:
                files.add(java_file)
                break
        
        # Check method or variable references (basic)
        for _, node in ast.filter(javalang.tree.MethodInvocation):
            if node.member in error_text or node.qualifier in error_text:
                files.add(java_file)
                break
        
        # If file is directly mentioned but no specific match, include it
        if java_file in candidate_files and java_file not in files:
            files.add(java_file)

    if not files.difference({pom_path}):
        print("Warning: No specific .java files matched via AST. Using pom.xml and repo map.")
    
    return list(files)

def run_aider_fix(error_text: str, files: List[str]) -> bool:
    """Run Aider to fix the error using the repo map and targeted files."""
    if not files:
        print("No files to fix. Cannot proceed with Aider.")
        return False
    
    message = (
        f"Fix this Maven compilation or test error within {TARGET_SUBDIR} using the repository map:\n\n"
        f"{error_text}\n\n"
        "Modify the provided files (e.g., pom.xml, Java source) to resolve the issue. "
        "The repo map should help identify dependencies or related code within the subdirectory."
    )
    
    cmd = AIDER_CMD + ["--message", message] + files
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
    print(f"Starting Aider-driven agent with ASTs focused on {TARGET_SUBDIR}...")
    subdir_files = get_subdir_files(TARGET_SUBDIR)
    print(f"Initial files in scope: {subdir_files}")
    
    for i in range(1, MAX_ITERATIONS + 1):
        print(f"Iteration {i}: Compiling and testing via Aider...")
        compile_success, test_success = run_maven_via_aider(subdir_files)
        
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
        files_to_fix = get_relevant_files_from_error(error_text, subdir_files)
        print(f"Iteration {i}: Targeting files: {files_to_fix}")
        
        print(f"Iteration {i}: Running Aider to fix with repo map...")
        if not run_aider_fix(error_text, files_to_fix):
            print(f"Iteration {i}: Aider failed to apply a fix. Continuing to next iteration.")
        
        import time
        time.sleep(1)
    
    print(f"Maximum iterations ({MAX_ITERATIONS}) reached. Could not resolve all issues.")
    print(f"Check {LOG_FILE} and {ERROR_LOG} for details.")
    sys.exit(1)

if __name__ == "__main__":
    main()
