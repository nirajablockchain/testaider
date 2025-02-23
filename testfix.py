import subprocess
import os
import sys
import re
from typing import Optional, List, Set, Dict
import javalang
from collections import Counter

# Configuration
MAX_ITERATIONS = 10
PROJECT_DIR = "."
LOG_FILE = "build_log.txt"
ERROR_LOG = "error_log.txt"
AIDER_CMD = ["aider", "--script", "--no-auto-commit", "--yes"]
MAX_ERROR_LINES = 50  # Limit error lines sent to Aider

def parse_java_file(file_path: str) -> Optional[javalang.ast.Node]:
    """Parse a Java file into an AST."""
    try:
        with open(file_path, "r") as f:
            source = f.read()
        return javalang.parse.parse(source)
    except (javalang.parser.JavaSyntaxError, FileNotFoundError) as e:
        print(f"Failed to parse {file_path}: {e}")
        return None

def trim_error_log(error_text: str) -> str:
    """Trim a huge error log to the most relevant parts."""
    lines = error_text.splitlines()
    error_lines = []
    seen_errors: Set[str] = set()
    
    # Collect unique errors with context
    for i, line in enumerate(lines):
        if "[ERROR]" in line:
            error_block = "".join(lines[i:i+6])  # 5 lines of context
            error_signature = re.sub(r":\d+:", ":<line>:", line)  # Normalize line numbers
            if error_signature not in seen_errors:
                seen_errors.add(error_signature)
                error_lines.append(error_block)
                if len(error_lines) * 6 >= MAX_ERROR_LINES:
                    break
    
    trimmed = "".join(error_lines)
    if len(lines) > MAX_ERROR_LINES and trimmed:
        trimmed += "\n[Note: Error log truncated. Showing first unique errors only.]"
    return trimmed

def get_relevant_files(error_text: str) -> List[str]:
    """Use grep-like regex and ASTs to find relevant files from trimmed error text."""
    files: Set[str] = set()
    pom_path = os.path.join(PROJECT_DIR, "pom.xml")
    if os.path.exists(pom_path):
        files.add(pom_path)

    # Grep: Extract file paths
    file_matches = re.findall(r"(\S+\.java):(?:\d+):", error_text)
    java_files = {os.path.join(PROJECT_DIR, f.strip("/")) for f in file_matches if os.path.exists(os.path.join(PROJECT_DIR, f.strip("/")))}

    # AST: Refine file list
    for java_file in java_files:
        files.add(java_file)
        ast = parse_java_file(java_file)
        if not ast:
            continue
        
        for _, node in ast.filter(javalang.tree.Import):
            if node.path in error_text:
                files.add(java_file)
        
        for _, node in ast.filter(javalang.tree.ClassDeclaration):
            if node.name in error_text:
                files.add(java_file)

    if not files:
        print("Warning: No specific files identified. Using pom.xml as fallback.")
        if os.path.exists(pom_path):
            files.add(pom_path)

    return list(files)

def run_maven_build_and_test() -> tuple[bool, bool]:
    """Run Maven compile and test, return (compile_success, test_success)."""
    with open(LOG_FILE, "w") as log:
        compile_result = subprocess.run(
            ["mvn", "clean", "compile", "-f", os.path.join(PROJECT_DIR, "pom.xml")],
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True
        )
    
    compile_success = compile_result.returncode == 0
    test_success = False
    
    if compile_success:
        with open(LOG_FILE, "a") as log:
            test_result = subprocess.run(
                ["mvn", "test", "-f", os.path.join(PROJECT_DIR, "pom.xml")],
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True
            )
        test_success = test_result.returncode == 0
    
    return compile_success, test_success

def extract_errors() -> Optional[str]:
    """Extract and trim error lines from the build log."""
    if not os.path.exists(LOG_FILE):
        return None
    
    with open(LOG_FILE, "r") as log:
        lines = log.readlines()
    
    error_lines = []
    for i, line in enumerate(lines):
        if "[ERROR]" in line:
            error_lines.extend(lines[i:i+6])
    
    with open(ERROR_LOG, "w") as err_log:
        err_log.writelines(error_lines)
    
    if not error_lines:
        return None
    
    full_error_text = "".join(error_lines)
    return trim_error_log(full_error_text)

def run_aider_fix(error_text: str, files: List[str]) -> bool:
    """Run Aider to fix the error, targeting specific files."""
    if not files:
        print("No files to fix. Cannot proceed with Aider.")
        return False
    
    message = (
        "Fix this Maven compilation or test error:\n\n"
        f"{error_text}\n\n"
        "Modify the provided files (e.g., pom.xml, Java source) to resolve the issue."
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
    print("Starting optimized agent for huge logs with Aider, ASTs, and grep...")
    
    for i in range(1, MAX_ITERATIONS + 1):
        print(f"Iteration {i}: Building and testing project...")
        compile_success, test_success = run_maven_build_and_test()
        
        if compile_success and test_success:
            print(f"Build and tests succeeded after {i} iterations!")
            os.remove(LOG_FILE)
            os.remove(ERROR_LOG) if os.path.exists(ERROR_LOG) else None
            sys.exit(0)
        
        error_text = extract_errors()
        if not error_text:
            print("No errors found, but build or tests failed. Check logs manually.")
            break
        
        print(f"Iteration {i}: Trimmed errors:\n{error_text}")
        files_to_fix = get_relevant_files(error_text)
        print(f"Iteration {i}: Targeting files: {files_to_fix}")
        
        print(f"Iteration {i}: Running Aider to fix...")
        if not run_aider_fix(error_text, files_to_fix):
            print(f"Iteration {i}: Aider failed to apply a fix. Continuing to next iteration.")
        
        import time
        time.sleep(1)
    
    print(f"Maximum iterations ({MAX_ITERATIONS}) reached. Could not resolve all issues.")
    print(f"Check {LOG_FILE} and {ERROR_LOG} for details.")
    sys.exit(1)

if __name__ == "__main__":
    main()
