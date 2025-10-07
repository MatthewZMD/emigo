#!/usr/bin/env python

"""
Repository Mapping and Analysis.

This module provides functionality to analyze a code repository, identify key
definitions and references using tree-sitter, and generate a concise "map"
of the codebase structure and relevant code snippets. This map is intended
to be included in the context provided to a Large Language Model (LLM) to
give it a better understanding of the project structure.

Based on code from the Aider project (https://github.com/paul-gauthier/aider),
this module implements:
- File discovery, respecting `.gitignore` and excluding binary/ignored files.
- Tag generation (definitions and references) using tree-sitter queries and
  pygments as a fallback.
- Caching of tags using `diskcache` to speed up repeated analysis.
- A ranking algorithm (PageRank) applied to the code dependency graph to
  identify the most relevant files and code elements based on context
  (e.g., files currently in chat, mentioned identifiers).
- Rendering of ranked code snippets using `grep-ast`'s `TreeContext`.
- Pruning the final map to fit within a specified token limit.

The main class `RepoMapper` is intended to be used by `session.py` to manage
the map generation for a specific user session. It also includes a command-line
interface for standalone usage and debugging.
"""

import argparse
import math
import os
import sys
import time
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import List, Dict, Set, Tuple, Union

import tiktoken
from diskcache import Cache
from grep_ast import TreeContext, filename_to_lang
from pygments.lexers import guess_lexer_for_filename
from pygments.token import Token
import networkx as nx # Import networkx here

# --- Configuration Constants ---

IGNORED_DIRS = [
    r'\.emigo_repomap$',
    r'\.aider.*$',
    r'\.(git|hg|svn)$',
    r'__pycache__$',
    r'node_modules$',
    r'(\.venv|venv|\.env|env)$',
    r'(build|dist)$',
    r'vendor$'
]

BINARY_EXTS = {
    # Images
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.ico', '.svg',
    # Media
    '.mp3', '.mp4', '.mov', '.avi', '.mkv', '.wav',
    # Archives
    '.zip', '.tar', '.gz', '.bz2', '.7z', '.rar',
    # Documents
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    # Other binaries
    '.exe', '.dll', '.so', '.o', '.a', '.class', '.jar',
    # Logs/Temp
    '.log', '.tmp', '.swp'
}

# Used in is_important
ROOT_IMPORTANT_FILES_LIST = [
    # Version Control
    ".gitignore",
    ".gitattributes",
    # Documentation
    "README",
    "README.md",
    "README.txt",
    "README.rst",
    "CONTRIBUTING",
    "CONTRIBUTING.md",
    "CONTRIBUTING.txt",
    "CONTRIBUTING.rst",
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "CHANGELOG",
    "CHANGELOG.md",
    "CHANGELOG.txt",
    "CHANGELOG.rst",
    "SECURITY",
    "SECURITY.md",
    "SECURITY.txt",
    "CODEOWNERS",
    # Package Management and Dependencies
    "requirements.txt",
    "Pipfile",
    "Pipfile.lock",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "npm-shrinkwrap.json",
    "Gemfile",
    "Gemfile.lock",
    "composer.json",
    "composer.lock",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "build.sbt",
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Cargo.lock",
    "mix.exs",
    "rebar.config",
    "project.clj",
    "Podfile",
    "Cartfile",
    "dub.json",
    "dub.sdl",
    # Configuration and Settings
    ".env",
    ".env.example",
    ".editorconfig",
    "tsconfig.json",
    "jsconfig.json",
    ".babelrc",
    "babel.config.js",
    ".eslintrc",
    ".eslintignore",
    ".prettierrc",
    ".stylelintrc",
    "tslint.json",
    ".pylintrc",
    ".flake8",
    ".rubocop.yml",
    ".scalafmt.conf",
    ".dockerignore",
    ".gitpod.yml",
    "sonar-project.properties",
    "renovate.json",
    "dependabot.yml",
    ".pre-commit-config.yaml",
    "mypy.ini",
    "tox.ini",
    ".yamllint",
    "pyrightconfig.json",
    # Build and Compilation
    "webpack.config.js",
    "rollup.config.js",
    "parcel.config.js",
    "gulpfile.js",
    "Gruntfile.js",
    "build.xml",
    "build.boot",
    "project.json",
    "build.cake",
    "MANIFEST.in",
    # Testing
    "pytest.ini",
    "phpunit.xml",
    "karma.conf.js",
    "jest.config.js",
    "cypress.json",
    ".nycrc",
    ".nycrc.json",
    # CI/CD
    ".travis.yml",
    ".gitlab-ci.yml",
    "Jenkinsfile",
    "azure-pipelines.yml",
    "bitbucket-pipelines.yml",
    "appveyor.yml",
    "circle.yml",
    ".circleci/config.yml",
    ".github/dependabot.yml",
    "codecov.yml",
    ".coveragerc",
    # Docker and Containers
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.override.yml",
    # Cloud and Serverless
    "serverless.yml",
    "firebase.json",
    "now.json",
    "netlify.toml",
    "vercel.json",
    "app.yaml",
    "terraform.tf",
    "main.tf",
    "cloudformation.yaml",
    "cloudformation.json",
    "ansible.cfg",
    "kubernetes.yaml",
    "k8s.yaml",
    # Database
    "schema.sql",
    "liquibase.properties",
    "flyway.conf",
    # Framework-specific
    "next.config.js",
    "nuxt.config.js",
    "vue.config.js",
    "angular.json",
    "gatsby-config.js",
    "gridsome.config.js",
    # API Documentation
    "swagger.yaml",
    "swagger.json",
    "openapi.yaml",
    "openapi.json",
    # Development environment
    ".nvmrc",
    ".ruby-version",
    ".python-version",
    "Vagrantfile",
    # Quality and metrics
    ".codeclimate.yml",
    "codecov.yml",
    # Documentation
    "mkdocs.yml",
    "_config.yml",
    "book.toml",
    "readthedocs.yml",
    ".readthedocs.yaml",
    # Package registries
    ".npmrc",
    ".yarnrc",
    # Linting and formatting
    ".isort.cfg",
    ".markdownlint.json",
    ".markdownlint.yaml",
    # Security
    ".bandit",
    ".secrets.baseline",
    # Misc
    ".pypirc",
    ".gitkeep",
    ".npmignore",
]

NORMALIZED_ROOT_IMPORTANT_FILES = set(os.path.normpath(path) for path in ROOT_IMPORTANT_FILES_LIST)

# Import tree-sitter related functions directly
try:
    from grep_ast.tsl import get_language, get_parser
except ImportError as e:
    raise ImportError("grep-ast with tree-sitter bindings is required. Try: pip install grep-ast") from e

# --- Constants ---

TAGS_CACHE_DIR = ".emigo_repomap"
TagInfo = Tuple[str, str, int, str, str] # (rel_fname, abs_fname, line, name, kind)
FileNamePlaceholder = Tuple[str] # (rel_fname,)

# --- Utility Functions ---

def read_text(filename, encoding="utf-8", errors="ignore"):
    """Reads a file and returns its content, returning None on error."""
    try:
        with open(str(filename), "r", encoding=encoding, errors=errors) as f:
            return f.read()
    except (FileNotFoundError, IsADirectoryError, OSError, UnicodeError):
        return None # Fail silently on read errors


def get_rel_fname(fname, root):
    """Gets the relative path of fname from the root."""
    try:
        return os.path.relpath(fname, root)
    except ValueError:
        # Handle cases where fname and root are on different drives (Windows)
        return fname


# --- Important Files Logic ---

def is_important(file_path):
    """Checks if a file path is considered important based on defined constants."""
    file_name = os.path.basename(file_path)
    dir_name = os.path.normpath(os.path.dirname(file_path))
    normalized_path = os.path.normpath(file_path)

    # Check for GitHub Actions workflow files
    if dir_name == os.path.normpath(".github/workflows") and file_name.endswith((".yml", ".yaml")):
        return True

    return normalized_path in NORMALIZED_ROOT_IMPORTANT_FILES

def filter_important_files(file_paths):
    """Filter a list of file paths to return only those considered important."""
    return [fp for fp in file_paths if is_important(fp)]


# --- Core Logic ---

class RepoMap:
    warned_files = set()

    def __init__(
        self,
        root,
        map_tokens=4096,
        verbose=False,
        tokenizer_name="cl100k_base",
        force_refresh=False,
        map_mul_no_files=4,
    ):
        self.verbose = verbose
        self.root = os.path.abspath(root)
        self.max_map_tokens = map_tokens
        self.force_refresh = force_refresh
        self.tokenizer = tiktoken.get_encoding(tokenizer_name) # Let exceptions propagate
        self.map_mul_no_files = map_mul_no_files

        self.TAGS_CACHE = self.load_tags_cache()
        self.tree_cache = {}
        self.tree_context_cache = {}
        self.map_processing_time = 0
        # Store scanned tag info globally within the instance
        self._all_tags: List[TagInfo] = []
        self._defines: Dict[str, Set[str]] = defaultdict(set) # name -> {rel_fname, ...}
        self._references: Dict[str, List[str]] = defaultdict(list) # name -> [rel_fname, ...]
        self._definitions: Dict[Tuple[str, str], Set[TagInfo]] = defaultdict(set) # (rel_fname, name) -> {TagInfo, ...}
        self._scanned_fnames: Set[str] = set() # Set of relative filenames scanned

    def token_count(self, text):
        """Counts tokens using the tiktoken tokenizer."""
        if not isinstance(text, str):
            text = str(text)
        return len(self.tokenizer.encode(text))

    def get_repo_map(self, chat_files, other_files):
        """Generates the repository map string."""
        if self.max_map_tokens <= 0 or not (other_files or chat_files):
            return ""

        start_time = time.time()
        # Ensure tags are scanned before ranking
        # Pass relative paths for scanning
        all_rel_fnames_to_scan = {get_rel_fname(f, self.root) for f in (set(chat_files) | set(other_files)) if Path(f).is_file()}
        self._scan_all_tags(all_rel_fnames_to_scan)

        # Calculate dynamic token budget
        current_max_map_tokens = self.max_map_tokens
        if not chat_files and self.map_mul_no_files > 1:
             current_max_map_tokens = int(self.max_map_tokens * self.map_mul_no_files)
             if self.verbose:
                 print(f"No chat files; increasing map token target to {current_max_map_tokens}", file=sys.stderr)

        files_listing = self.get_ranked_tags_map_uncached(
            chat_files, other_files, current_max_map_tokens # Use dynamic budget
        )
        self.map_processing_time = time.time() - start_time

        if not files_listing:
            return ""

        map_token_count = self.token_count(files_listing)
        print(f"Repo Map ({map_token_count} tokens) processed in {self.map_processing_time:.2f}s", file=sys.stderr)

        return files_listing

    def load_tags_cache(self):
        """Loads the tags cache from disk or initializes it."""
        path = Path(self.root) / TAGS_CACHE_DIR
        try:
            # Attempt to initialize disk cache
            cache = Cache(path)
            _ = len(cache) # Basic check
            return cache
        except Exception:
            # Fallback to in-memory dict if disk cache fails
            return dict() # Return a simple dict as the cache interface

    def get_mtime(self, fname):
        """Gets the modification time of a file."""
        try:
            return os.path.getmtime(fname)
        except FileNotFoundError:
            return None # Return None if file not found

    def get_tags(self, fname, rel_fname):
        """Gets tags for a file, using the cache if possible."""
        file_mtime = self.get_mtime(fname)
        if file_mtime is None:
            return []

        cache_key = fname # Use absolute path as key
        cached_val = None # Initialize
        if hasattr(self.TAGS_CACHE, 'get'):
            try:
                # Attempt to retrieve from cache
                cached_val = self.TAGS_CACHE.get(cache_key)
            except TypeError as e:
                # Handle potential unpickling errors (e.g., type definition changed)
                if self.verbose:
                    print(f"Warning: Cache read error for {fname} (likely format change): {e}. Regenerating.", file=sys.stderr)
                # Treat as cache miss by leaving cached_val as None
                # Explicitly delete the bad cache entry to prevent repeated warnings
                if hasattr(self.TAGS_CACHE, 'delete'):
                    try:
                        self.TAGS_CACHE.delete(cache_key)
                        if self.verbose:
                            print(f"Info: Deleted invalid cache entry for {fname}.", file=sys.stderr)
                    except Exception as delete_e:
                        if self.verbose:
                            print(f"Warning: Failed to delete invalid cache entry for {fname}: {delete_e}", file=sys.stderr)
            except Exception as e:
                # Handle other potential cache read errors
                if self.verbose:
                    print(f"Warning: Unexpected cache read error for {fname}: {e}. Regenerating.", file=sys.stderr)
                # Treat as cache miss

        # Check cache validity (only if cached_val was successfully retrieved)
        if not self.force_refresh and isinstance(cached_val, dict) and cached_val.get("mtime") == file_mtime:
            # Ensure 'data' exists and is a list before returning
            data = cached_val.get("data")
            if isinstance(data, list):
                # Validate tag format (simple check)
                if not data or (isinstance(data[0], tuple) and len(data[0]) == 5):
                    return data
                elif self.verbose:
                    print(f"Warning: Invalid cached tag format for {fname}. Regenerating.", file=sys.stderr)
            elif self.verbose:
                 print(f"Warning: Invalid cache data format for {fname}. Regenerating.", file=sys.stderr)
            # Fall through to regenerate if data is not a list or format is wrong

        # Cache miss or invalid: Generate tags
        data = list(self.get_tags_raw(fname, rel_fname))

        # Update cache if it's a real cache object
        if hasattr(self.TAGS_CACHE, '__setitem__'):
            try:
                cache_entry = {"mtime": file_mtime, "data": data}
                self.TAGS_CACHE[cache_key] = cache_entry
            except Exception as e:
                if self.verbose:
                    print(f"Warning: Cache write error for {fname}: {e}", file=sys.stderr)
                # Ignore cache write errors silently otherwise

        return data

    def get_tags_raw(self, fname, rel_fname):
        """Generates tags using tree-sitter and pygments fallback."""
        lang = filename_to_lang(fname)
        if not lang:
            return

        language, parser = None, None
        try:
            language = get_language(lang)
            parser = get_parser(lang)
        except Exception:
            return # Silently skip if parser not found

        code = read_text(fname)
        if not code:
            return

        # Tree-sitter tags
        query_scm_path = get_scm_fname(lang)
        tree_sitter_defs_found = False
        tree_sitter_refs_found = False
        if query_scm_path:
            try:
                query_scm = query_scm_path.read_text(encoding='utf-8')
                tree = parser.parse(bytes(code, "utf-8"))
                query = language.query(query_scm)
                captures = query.captures(tree.root_node)

                # Use the recommended way to iterate captures for grep-ast >= 0.10
                all_nodes = []
                if hasattr(captures, 'items'): # Check if it's dict-like (older grep-ast)
                    for tag_name, nodes in captures.items():
                        all_nodes.extend([(node, tag_name) for node in nodes])
                else: # Assume it's list-like (newer grep-ast)
                    all_nodes = list(captures)

                for node, tag_name in all_nodes:
                    kind = None
                    if tag_name.startswith("name.definition."):
                        kind = "def"
                        tree_sitter_defs_found = True
                    elif tag_name.startswith("name.reference."):
                        kind = "ref"
                        tree_sitter_refs_found = True

                    if kind:
                        try:
                            name_text = node.text.decode("utf-8")
                            # Use tuple directly: (rel_fname, abs_fname, line, name, kind)
                            yield (rel_fname, fname, node.start_point[0], name_text, kind)
                        except (AttributeError, UnicodeDecodeError):
                            continue # Skip invalid nodes
            except Exception as e:
                 if self.verbose: print(f"Tree-sitter error in {fname}: {e}", file=sys.stderr)
                 pass # Silently ignore tree-sitter errors

        # Pygments fallback for references if tree-sitter didn't find any or wasn't used
        # Also use pygments if only defs were found (some TS grammars lack refs)
        if not tree_sitter_refs_found and tree_sitter_defs_found:
            try:
                lexer = guess_lexer_for_filename(fname, code)
                tokens = lexer.get_tokens(code)
                for token_type, token_text in tokens:
                    if token_type in Token.Name:
                        # Use tuple directly: (rel_fname, abs_fname, line, name, kind)
                        yield (rel_fname, fname, -1, token_text, "ref") # Line -1 for pygments
            except Exception as e:
                 if self.verbose: print(f"Pygments error in {fname}: {e}", file=sys.stderr)
                 pass # Silently ignore pygments errors

    def _scan_all_tags(self, fnames_to_scan: Set[str]):
        """
        Scans specified relative filenames for tags, updating internal state.
        fnames_to_scan should contain relative paths.
        """
        if self.force_refresh:
            self._all_tags = []
            self._defines = defaultdict(set)
            self._references = defaultdict(list)
            self._definitions = defaultdict(set)
            self._scanned_fnames = set()
            files_to_process = fnames_to_scan # Rescan everything requested
            if self.verbose: print(f"Force refresh: Rescanning {len(files_to_process)} files.", file=sys.stderr)
        else:
            files_to_process = fnames_to_scan - self._scanned_fnames
            if not files_to_process:
                return # Nothing new to scan
            if self.verbose: print(f"Scanning {len(files_to_process)} new/updated files.", file=sys.stderr)

        new_tags = []
        processed_rel_fnames = set()
        for rel_fname in sorted(list(files_to_process)):
            abs_fname = os.path.abspath(os.path.join(self.root, rel_fname))
            if not Path(abs_fname).is_file():
                if abs_fname not in self.warned_files:
                    # Keep minimal warning for non-existent files
                    print(f"Warning: Skipping non-file {abs_fname} (relative: {rel_fname})", file=sys.stderr)
                    self.warned_files.add(abs_fname)
                continue

            try:
                tags = self.get_tags(abs_fname, rel_fname) # get_tags handles caching
                new_tags.extend(tags)
                processed_rel_fnames.add(rel_fname) # Track successfully processed files by relative path
            except Exception as e:
                print(f"Error getting tags for {rel_fname}: {e}", file=sys.stderr)


        self._scanned_fnames.update(processed_rel_fnames)

        # Update global tag structures only if new tags were found or refreshing
        if new_tags or self.force_refresh:
            if self.force_refresh:
                 self._all_tags = new_tags # Replace if refreshing
            else:
                 # If not refreshing, we need to update existing structures carefully
                 # Remove old tags belonging to the re-processed files
                 self._all_tags = [tag for tag in self._all_tags if tag[0] not in processed_rel_fnames]
                 self._all_tags.extend(new_tags) # Append new tags

            # Rebuild defines/references/definitions from the potentially updated _all_tags
            self._defines = defaultdict(set)
            self._references = defaultdict(list)
            self._definitions = defaultdict(set)
            for tag_tuple in self._all_tags:
                # Unpack the tuple: (rel_fname, abs_fname, line, name, kind)
                rel_fname, _, _, name, kind = tag_tuple
                if kind == "def":
                    self._defines[name].add(rel_fname)
                    # Store the full tag tuple under the (rel_fname, name) key
                    self._definitions[(rel_fname, name)].add(tag_tuple)
                elif kind == "ref":
                    self._references[name].append(rel_fname)

            # Fallback: Use definitions as references if no explicit references found
            # This helps languages where tree-sitter tags might only provide defs
            if not self._references and self._defines:
                if self.verbose: print("No references found, using definitions as fallback references.", file=sys.stderr)
                self._references = {k: list(v) for k, v in self._defines.items()}


    def get_ranked_tags(self, chat_fnames: List[str], other_fnames: List[str]) -> List[Union[TagInfo, FileNamePlaceholder]]:
        """
        Ranks definitions based on references and chat context using PageRank.

        Args:
            chat_fnames: List of absolute paths to files in chat.
            other_fnames: List of absolute paths to other files in the repo.

        Returns:
            A sorted list containing TagInfo tuples for ranked definitions
            and FileNamePlaceholder tuples for important/unranked files,
            ordered by relevance (highest first).
        """
        all_abs_fnames = set(chat_fnames) | set(other_fnames)
        # Filter chat files to ensure they exist before getting rel_fname
        chat_rel_fnames = {get_rel_fname(f, self.root) for f in chat_fnames if Path(f).is_file()}
        other_rel_fnames = {get_rel_fname(f, self.root) for f in other_fnames if Path(f).is_file()}
        all_rel_fnames = chat_rel_fnames | other_rel_fnames

        # Ensure tags for all relevant files are loaded/scanned
        self._scan_all_tags(all_rel_fnames)

        # Use the scanned data stored in the instance attributes
        defines = self._defines
        references = self._references
        definitions = self._definitions # (rel_fname, name) -> {TagInfo, ...}
        scanned_rel_fnames = self._scanned_fnames # All rel_fnames successfully scanned

        # --- Build Dependency Graph ---
        G = nx.MultiDiGraph()
        # Use only identifiers that have both definitions and references recorded
        idents = set(defines.keys()) & set(references.keys())

        # Add nodes for all scanned files first
        for rel_fname in scanned_rel_fnames:
             G.add_node(rel_fname)

        for ident in idents:
            # Ensure definers/referencers are among the files we actually scanned
            definers = defines[ident] & scanned_rel_fnames
            # Count references per file, only considering scanned files
            referencers_counts = Counter(ref for ref in references[ident] if ref in scanned_rel_fnames)

            if not definers or not referencers_counts:
                continue # Skip identifiers with no valid defs or refs in scanned files

            # --- Aider-like Weighting ---
            mul = 1.0
            # Basic weighting adjustments (can be expanded)
            is_snake = ("_" in ident) and any(c.isalpha() for c in ident)
            is_camel = any(c.isupper() for c in ident) and any(c.islower() for c in ident)
            # Boost important-looking identifiers (heuristic)
            if (is_snake or is_camel) and len(ident) >= 6:
                mul *= 5
            # Penalize private/internal-like identifiers
            if ident.startswith("_"):
                mul *= 0.1
            # Penalize identifiers defined in many places (less specific)
            if len(defines[ident]) > 5:
                mul *= 0.1

            for referencer, num_refs in referencers_counts.items():
                # Boost references from files currently in chat
                use_mul = mul * 50 if referencer in chat_rel_fnames else mul
                # Scale down impact of high-frequency references (sqrt)
                weight = math.sqrt(num_refs) * use_mul

                for definer in definers:
                    # Add edge from referencer to definer for this identifier
                    G.add_edge(referencer, definer, weight=weight, ident=ident)

        # --- Run PageRank ---
        ranked_files = {} # Store file ranks: rel_fname -> rank
        if G and G.nodes: # Check if graph is not empty
            # Calculate personalization based on chat files present in the graph
            personalization = {
                rel_fname: 1.0 # Assign equal high personalization to chat files
                for rel_fname in chat_rel_fnames if rel_fname in G
            }
            # Normalize personalization values
            pers_sum = sum(personalization.values())
            if pers_sum > 0:
                 personalization = {k: v / pers_sum for k, v in personalization.items()}

            try:
                # Use personalization if available
                pers_args = dict(personalization=personalization, dangling=personalization) if personalization else {}
                ranked_files = nx.pagerank(G, weight="weight", alpha=0.85, **pers_args)
            except ZeroDivisionError:
                 print("Warning: PageRank ZeroDivisionError. Falling back to basic ranking.", file=sys.stderr)
                 # Fallback: Rank nodes equally, respecting personalization if PageRank fails
                 num_nodes = len(G)
                 base_rank = 1.0 / num_nodes if num_nodes > 0 else 0
                 ranked_files = {node: personalization.get(node, base_rank) for node in G}
                 # Normalize if personalization was used
                 total_rank = sum(ranked_files.values())
                 if personalization and total_rank > 0:
                     ranked_files = {node: r / total_rank for node, r in ranked_files.items()}
            except Exception as e:
                print(f"Warning: PageRank failed ({e}). Falling back to basic ranking.", file=sys.stderr)
                num_nodes = len(G)
                base_rank = 1.0 / num_nodes if num_nodes > 0 else 0
                ranked_files = {node: personalization.get(node, base_rank) for node in G}

        # Ensure all scanned files have a rank (default to 0 if missing)
        for rel_fname in scanned_rel_fnames:
            ranked_files.setdefault(rel_fname, 0.0)

        # --- Distribute File Rank to Definitions ---
        ranked_definitions = defaultdict(float) # (rel_fname, ident) -> accumulated_rank
        for src in G.nodes:
            src_rank = ranked_files.get(src, 0.0)
            if src_rank == 0.0: continue

            # Calculate total weight of outgoing edges from this source file
            total_weight = sum(data["weight"] for _, _, data in G.out_edges(src, data=True) if "weight" in data)

            if total_weight > 0:
                # Distribute rank based on edge weight
                for _, dst, data in G.out_edges(src, data=True):
                    ident = data.get("ident")
                    weight = data.get("weight", 0)
                    if ident and weight > 0:
                        # Rank contribution = file_rank * (edge_weight / total_outgoing_weight)
                        rank_share = src_rank * (weight / total_weight)
                        ranked_definitions[(dst, ident)] += rank_share
            # else: If no outgoing edges with weight, rank is not distributed

        # --- Prepare Final Ranked List ---
        # Convert ranked definitions to TagInfo tuples
        ranked_tags: List[TagInfo] = []
        # Sort definitions by accumulated rank, then alphabetically for stability
        sorted_ranked_defs = sorted(
            ranked_definitions.items(),
            key=lambda item: (item[1], item[0][0], item[0][1]), # Sort by rank (desc), filename, ident
            reverse=True
        )

        processed_tags = set() # Keep track of (rel_fname, line, name) to avoid duplicates if multiple refs point to same def line
        fnames_with_ranked_tags = set() # Keep track of files included via ranked tags

        for (rel_fname, ident), rank in sorted_ranked_defs:
            # Skip definitions from files already in chat
            if rel_fname in chat_rel_fnames:
                continue

            # Get all TagInfo tuples for this definition (usually just one)
            def_tags = definitions.get((rel_fname, ident), set())
            for tag_info in def_tags:
                # tag_info = (rel_fname, abs_fname, line, name, kind)
                tag_key = (tag_info[0], tag_info[2], tag_info[3]) # (rel_fname, line, name)
                if tag_key not in processed_tags:
                    ranked_tags.append(tag_info)
                    processed_tags.add(tag_key)
                    fnames_with_ranked_tags.add(rel_fname)

        # --- Include Important Files and Other Unranked Files ---
        # Get important files (absolute paths)
        important_files_abs = filter_important_files(other_fnames)
        # Convert to relative paths and filter out chat files and those already included
        important_rel_fnames = {
            get_rel_fname(f, self.root) for f in important_files_abs
        } - chat_rel_fnames - fnames_with_ranked_tags

        # Create placeholders for important files
        important_file_placeholders: List[FileNamePlaceholder] = [
            (rel_f,) for rel_f in sorted(list(important_rel_fnames))
        ]

        # Include other files from the repo that weren't ranked/important (e.g., only had refs)
        # Sort them by their file rank (descending)
        other_unranked_rel_fnames = scanned_rel_fnames - chat_rel_fnames - fnames_with_ranked_tags - important_rel_fnames
        sorted_other_unranked = sorted(
             other_unranked_rel_fnames,
             key=lambda f: ranked_files.get(f, 0.0),
             reverse=True
        )
        other_file_placeholders: List[FileNamePlaceholder] = [
            (rel_f,) for rel_f in sorted_other_unranked
        ]

        # Combine: Important files first, then ranked tags, then other files
        final_ranked_list = important_file_placeholders + ranked_tags + other_file_placeholders

        if self.verbose:
            print(f"Ranking complete: {len(important_file_placeholders)} important files, {len(ranked_tags)} ranked tags, {len(other_file_placeholders)} other files.", file=sys.stderr)

        return final_ranked_list


    def get_related_files(self, target_fnames: List[str]) -> List[str]:
        """
        Finds files related to the target files.

        This includes:
        1. Files that reference identifiers defined *in* the target files.
        2. Files that define identifiers referenced *by* the target files.

        Args:
            target_fnames: List of absolute paths to the target files.

        Returns:
            A sorted list of unique relative file paths related to the targets,
            excluding the target files themselves.
        """
        if not target_fnames:
            return []

        # --- Preprocessing ---
        # Convert absolute target paths to relative paths within the root
        target_rel_fnames = set()
        for f_abs in target_fnames:
            try:
                # Ensure the absolute path is valid and within the root before converting
                p_abs = Path(f_abs).resolve() # Resolve symlinks etc.
                if p_abs.is_file() and str(p_abs).startswith(self.root):
                    target_rel_fnames.add(get_rel_fname(str(p_abs), self.root))
                elif self.verbose:
                    print(f"Warning [get_related_files]: Skipping target file not found or outside root: {f_abs}", file=sys.stderr)
            except Exception as e:
                 if self.verbose:
                     print(f"Warning [get_related_files]: Error processing target file {f_abs}: {e}", file=sys.stderr)

        if not target_rel_fnames:
            if self.verbose: print("Warning [get_related_files]: No valid target files found after filtering.", file=sys.stderr)
            return []

        # Ensure tags are scanned (usually done by generate_map beforehand)
        # We rely on the instance attributes _all_tags, _defines, _references being populated.
        if not self._scanned_fnames and self.verbose:
             print("Warning [get_related_files]: Tag data seems empty. Was generate_map called first?", file=sys.stderr)
             # Optionally, trigger a scan here if needed, but it might be slow
             # self._scan_all_tags(target_rel_fnames) # Example: Scan at least targets if empty

        all_tags = self._all_tags
        defines = self._defines
        references = self._references

        related_files = set()

        # --- Part 1: Find files referencing definitions IN target files (Existing Logic) ---
        defs_in_targets = set()
        for tag in all_tags:
            # tag: (rel_fname, abs_fname, line, name, kind)
            if tag[0] in target_rel_fnames and tag[4] == 'def':
                defs_in_targets.add(tag[3]) # Add the identifier name

        # if self.verbose: print(f"[get_related_files] Definitions in targets: {defs_in_targets}", file=sys.stderr)

        for ident in defs_in_targets:
            # references[ident] contains list of rel_fnames that reference this ident
            for referencing_file in references.get(ident, []):
                related_files.add(referencing_file)
                # if self.verbose: print(f"  [Related Part 1] File '{referencing_file}' references '{ident}' (defined in target)", file=sys.stderr)


        # --- Part 2: Find files defining identifiers referenced BY target files (New Logic) ---
        refs_from_targets = set()
        for tag in all_tags:
            # tag: (rel_fname, abs_fname, line, name, kind)
            if tag[0] in target_rel_fnames and tag[4] == 'ref':
                refs_from_targets.add(tag[3]) # Add the identifier name being referenced

        # if self.verbose: print(f"[get_related_files] References from targets: {refs_from_targets}", file=sys.stderr)

        for ident in refs_from_targets:
            # defines[ident] contains set of rel_fnames where this ident is defined
            for defining_file in defines.get(ident, set()):
                related_files.add(defining_file)
                # if self.verbose: print(f"  [Related Part 2] File '{defining_file}' defines '{ident}' (referenced by target)", file=sys.stderr)

        # --- Combine and Filter ---
        # Remove the original target files from the results
        final_related_files = related_files - target_rel_fnames

        if self.verbose: print(f"[get_related_files] Found {len(final_related_files)} related files (excluding targets).", file=sys.stderr)

        # Return sorted list of unique relative filenames
        return sorted(list(final_related_files))

    def get_ranked_tags_map_uncached(
        self, chat_fnames: List[str], other_fnames: List[str], current_max_map_tokens: int
    ) -> str:
        """
        Generates the map string from ranked tags/files, fitting token limit.

        Args:
            chat_fnames: Absolute paths of files in chat.
            other_fnames: Absolute paths of other files.
            current_max_map_tokens: The token budget for the map.

        Returns:
            The formatted map string.
        """

        # Get ranked list of TagInfo and FileNamePlaceholder tuples
        # This list already excludes chat files and prioritizes important files.
        ranked_items = self.get_ranked_tags(chat_fnames, other_fnames)

        if not ranked_items:
            return ""

        # --- Binary search for optimal number of items (tags/files) ---
        num_items = len(ranked_items)
        low, high = 0, num_items
        best_tree, best_tokens = "", 0
        best_num_items = 0
        self.tree_cache.clear() # Clear render cache for this run

        # Heuristic start point for binary search (guess based on average token cost)
        # Assume ~25 tokens per tag/file on average? Highly variable.
        mid = min(int(current_max_map_tokens / 25), num_items) if num_items > 0 else 0

        iterations = 0
        max_iterations = int(math.log2(num_items)) + 5 if num_items > 0 else 0 # Limit iterations

        while low <= high and iterations < max_iterations:
            iterations += 1
            # Ensure mid is within bounds [0, num_items]
            mid = max(0, min(num_items, (low + high) // 2))

            if mid == 0:
                 # If mid is 0, test with 0 items (empty map)
                 current_items = []
                 num_tokens = 0
                 # If even 0 items is the best we can do, break
                 if best_tokens == 0: best_tree, best_tokens, best_num_items = "", 0, 0
                 # If we previously had a valid map, don't overwrite with empty unless necessary
                 # Decide based on whether low is already > 0 (meaning 1 item was too big)
                 if low > 0: break # Cannot fit even 1 item
                 # Otherwise, try increasing (low = mid + 1 below will handle this)

            else:
                 current_items = ranked_items[:mid]
                 # Pass the list of selected tags/placeholders to render
                 tree = self.to_tree(current_items)
                 num_tokens = self.token_count(tree)

            # --- Binary Search Logic ---
            # Check if this map is better than the current best
            is_better = False
            if num_tokens <= current_max_map_tokens:
                # It fits. Is it better (larger) than the previous best?
                if num_tokens >= best_tokens:
                    is_better = True
                # Or, is it very close to the target (within 5%) and better than a much smaller best?
                elif num_tokens > 0.95 * current_max_map_tokens and best_tokens < 0.90 * current_max_map_tokens:
                    is_better = True

            if is_better:
                best_tree, best_tokens, best_num_items = tree, num_tokens, mid

            # Adjust search range
            if num_tokens < current_max_map_tokens:
                low = mid + 1 # Try more items
            elif num_tokens > current_max_map_tokens:
                high = mid - 1 # Try fewer items
            else:
                # Exact match, we are done
                best_tree, best_tokens, best_num_items = tree, num_tokens, mid
                break

            # Optimization: Stop early if we have a decent map close to the limit
            if 0.95 * current_max_map_tokens < best_tokens <= current_max_map_tokens:
                 break

        if self.verbose:
            print(f"Selected map size: {best_tokens} tokens using {best_num_items}/{num_items} ranked items.", file=sys.stderr)
        return best_tree

    def render_tree(self, abs_fname, rel_fname, lois):
        """Renders code snippets around lines of interest (lois) for a file."""
        mtime = self.get_mtime(abs_fname)
        if mtime is None: return f"# Error: Cannot access {rel_fname}\n"

        # Ensure lois are unique and sorted integers >= 0
        lois_set = {line for line in lois if isinstance(line, int) and line >= 0}
        lois_tuple = tuple(sorted(list(lois_set)))
        if not lois_tuple: # No valid lines to render
             return "" # Return empty string, filename header added by to_tree

        render_key = (rel_fname, lois_tuple, mtime)
        render_key = (rel_fname, lois_tuple, mtime)
        if render_key in self.tree_cache:
            return self.tree_cache[render_key]

        # Check/Update TreeContext cache
        context_cache_entry = self.tree_context_cache.get(rel_fname)
        context = None
        if context_cache_entry and context_cache_entry.get("mtime") == mtime:
            context = context_cache_entry["context"]
        else:
            code = read_text(abs_fname)
            if code is None: return f"# Error: Cannot read {rel_fname}\n"
            if not code.endswith("\n"): code += "\n"
            try:
                context = TreeContext(
                    rel_fname, code, color=False, line_number=False, child_context=False,
                    last_line=False, margin=0, mark_lois=False, loi_pad=0,
                    show_top_of_file_parent_scope=False,
                )
                self.tree_context_cache[rel_fname] = {"context": context, "mtime": mtime}
            except Exception as e:
                 return f"# Error processing {rel_fname} for TreeContext: {e}\n"

        # Render using the cached or new context
        rendered_output = f"# Error formatting {rel_fname}\n" # Default error
        if context:
            try:
                # Use the validated lois_set
                context.lines_of_interest = lois_set
                context.add_context() # Recalculate context lines based on LOIs
                rendered_output = context.format()
                # Truncate long lines (e.g., minified code)
                rendered_output = "\n".join([line[:150] for line in rendered_output.splitlines()])

            except Exception as e:
                 rendered_output = f"# Error formatting {rel_fname} lines {lois_tuple}: {e}\n"
                 if self.verbose:
                     print(f"Warning: Error formatting {rel_fname} lines {lois_tuple}: {e}", file=sys.stderr)

        self.tree_cache[render_key] = rendered_output
        return rendered_output

    def to_tree(self, ranked_items: List[Union[TagInfo, FileNamePlaceholder]]):
        """
        Formats the selected ranked items (tags/files) into the map string.

        Args:
            ranked_items: A list of TagInfo and FileNamePlaceholder tuples.

        Returns:
            The formatted map string.
        """
        if not ranked_items: return ""

        output_parts = []
        # Group items by relative filename
        files_to_render: Dict[str, Dict] = defaultdict(lambda: {"abs_fname": None, "lois": set(), "is_placeholder": False})

        for item in ranked_items:
            if len(item) == 5 and isinstance(item[2], int): # Heuristic check for TagInfo
                tag_info: TagInfo = item
                rel_fname, abs_fname, line, _, _ = tag_info
                files_to_render[rel_fname]["abs_fname"] = abs_fname
                if line >= 0: # Only add valid line numbers
                    files_to_render[rel_fname]["lois"].add(line)
            elif len(item) == 1 and isinstance(item[0], str): # Check for FileNamePlaceholder
                placeholder: FileNamePlaceholder = item
                rel_fname = placeholder[0]
                # Mark as placeholder, abs_fname might be set later if a tag for the same file exists
                files_to_render[rel_fname]["is_placeholder"] = True
                if files_to_render[rel_fname]["abs_fname"] is None:
                     # Try to determine abs_fname if not already set by a tag
                     potential_abs = os.path.abspath(os.path.join(self.root, rel_fname))
                     if Path(potential_abs).is_file():
                          files_to_render[rel_fname]["abs_fname"] = potential_abs


        # Process the grouped files, maintaining original order as much as possible
        # We iterate through the original ranked_items to get the order,
        # but render based on the collected data in files_to_render.
        rendered_files = set()
        for item in ranked_items:
            rel_fname = item[0] # First element is always rel_fname
            if rel_fname in rendered_files:
                continue # Already rendered this file

            file_data = files_to_render[rel_fname]
            abs_fname = file_data["abs_fname"]
            lois = file_data["lois"]

            output_parts.append(f"\n{rel_fname}") # Add filename header

            if lois and abs_fname: # Render specific lines if LOIs exist
                output_parts.append(":\n") # Add colon after filename
                rendered_tree = self.render_tree(abs_fname, rel_fname, list(lois))
                output_parts.append(rendered_tree)
            elif file_data["is_placeholder"]: # Just list filename if it was a placeholder with no tags rendered
                 output_parts.append("\n") # Newline after filename
            else: # File had tags but no valid LOIs? Or error?
                 output_parts.append(" (error or no definitions)\n")

            rendered_files.add(rel_fname)


        # Join and add trailing newline
        full_output = "".join(output_parts)
        # Add final newline only if there's content
        return full_output + "\n" if full_output.strip() else ""


# --- Helper Functions ---

def get_scm_fname(lang):
    """Finds the tree-sitter query file for a language."""
    script_dir = Path(__file__).parent.resolve()
    # Prefer queries bundled with Emigo first
    emigo_query_path = script_dir / "queries" / f"{lang}-tags.scm"
    if emigo_query_path.is_file():
        return emigo_query_path
    # Fallback to aider's structure if needed (adjust path as necessary)
    aider_style_path = script_dir / "queries" / "tree-sitter-languages" / f"{lang}-tags.scm"
    if aider_style_path.is_file():
        return aider_style_path
    return None


# --- Public API Wrapper ---

class RepoMapper:
    """Manages repository analysis and map generation."""
    def __init__(self, root_dir, map_tokens=4096, tokenizer="cl100k_base", verbose=False, force_refresh=False, map_mul_no_files=4):
        self.root = os.path.abspath(root_dir)
        self.verbose = verbose # Keep verbose flag for potential debugging needs
        self.repomap = RepoMap(
            root=self.root,
            map_tokens=map_tokens,
            verbose=verbose,
            tokenizer_name=tokenizer,
            force_refresh=force_refresh,
            map_mul_no_files=map_mul_no_files # Pass multiplier
        )

    def _is_gitignored(self, path, matches_func=None):
        """Checks if a path is gitignored using a pre-parsed matcher."""
        # Ensure path is relative to root for gitignore matching
        try:
            rel_path = os.path.relpath(path, self.root)
            # gitignore_parser usually works with paths relative to the gitignore file (root)
            # It also expects POSIX-style paths
            return matches_func and matches_func(rel_path.replace(os.sep, '/'))
        except ValueError:
            # Path is likely outside the root (e.g., different drive)
            return False # Cannot be ignored by root gitignore

    def _get_gitignore_matcher(self):
        """Parses .gitignore and returns a matching function."""
        try:
            # Use Path object for checking existence
            gitignore_path = Path(self.root) / '.gitignore'
            if gitignore_path.is_file():
                # Lazy import inside try block
                from gitignore_parser import parse as parse_gitignore
                # Pass the Path object to parse
                with gitignore_path.open('r', encoding='utf-8') as f:
                    return parse_gitignore(f)
            else:
                 if self.verbose: print("No .gitignore found at root.", file=sys.stderr)
        except ImportError:
            if self.verbose: print("`gitignore-parser` not installed. .gitignore files will be ignored.", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Error parsing .gitignore: {e}", file=sys.stderr)
        # Return a function that never matches if no gitignore or error
        return lambda _: False

    def _find_src_files(self, directory):
        """Finds source code files recursively, respecting ignores."""
        src_files = []
        compiled_ignored_dirs = [re.compile(pattern) for pattern in IGNORED_DIRS]
        gitignore_matcher = self._get_gitignore_matcher()

        root_path_obj = Path(directory).resolve() # Ensure absolute path
        if not root_path_obj.is_dir():
            # Handle case where input is a single file
            if root_path_obj.is_file() and \
               root_path_obj.suffix.lower() not in BINARY_EXTS and \
               not self._is_gitignored(str(root_path_obj), gitignore_matcher):
                return [str(root_path_obj)]
            return []

        for root, dirs, files in os.walk(str(root_path_obj), topdown=True, onerror=lambda e: print(f"Warning: os.walk error: {e}", file=sys.stderr)):
            root_path = Path(root)
            # Filter ignored directories before recursing
            # Check absolute path for gitignore
            dirs[:] = [d for d in dirs if not (
                d.startswith('.') or # Hidden dirs
                any(pattern.search(d) for pattern in compiled_ignored_dirs) or # Pattern ignores
                self._is_gitignored(str(root_path / d), gitignore_matcher) # Gitignored dirs
            )]

            for file in files:
                file_path = root_path / file
                # Check file ignores using absolute path
                if not (file.startswith('.') or # Hidden files
                        file_path.suffix.lower() in BINARY_EXTS or # Binary extensions
                        self._is_gitignored(str(file_path), gitignore_matcher)): # Gitignored files
                    # Ensure it's a file (ignore symlinks to dirs, etc.)
                    try:
                        if file_path.is_file():
                            src_files.append(str(file_path))
                    except OSError as e: # Handle potential errors like broken symlinks
                         if self.verbose: print(f"Warning: Cannot check if {file_path} is file: {e}", file=sys.stderr)


        return src_files

    def generate_map(self, chat_files=None, force_refresh=None):
        """Generates the repository map string."""
        # Resolve chat files relative to root and ensure they are absolute
        chat_files_abs = []
        if chat_files:
            for f in chat_files:
                abs_f = os.path.abspath(os.path.join(self.root, f))
                # Check if it exists and is within the root directory
                if Path(abs_f).is_file() and abs_f.startswith(self.root):
                    chat_files_abs.append(abs_f)
                elif self.verbose:
                    print(f"Warning: Skipping chat file (not found or outside root): {f}", file=sys.stderr)

        if force_refresh is not None:
            self.repomap.force_refresh = force_refresh # Update underlying mapper if specified

        # Find all source files in the repository (returns absolute paths)
        all_repo_files_abs = self._find_src_files(self.root)
        if not all_repo_files_abs:
            if self.verbose: print("No source files found in repository.", file=sys.stderr)
            return "" # No source files found

        # Separate files into chat context vs. others
        chat_files_set = set(chat_files_abs)
        other_files_abs = [f for f in all_repo_files_abs if f not in chat_files_set]

        # Delegate map generation to the core RepoMap instance
        # Pass absolute paths for both lists
        return self.repomap.get_repo_map(
            chat_files=chat_files_abs,
            other_files=other_files_abs,
        )

    def get_related_files(self, target_files: List[str]) -> List[str]:
        """Finds files referencing identifiers defined in target_files."""
        # Resolve target files to absolute paths and filter for existing files within root
        target_files_abs = []
        for f in target_files:
             abs_f = os.path.abspath(os.path.join(self.root, f))
             if Path(abs_f).is_file() and abs_f.startswith(self.root):
                 target_files_abs.append(abs_f)

        if not target_files_abs:
            return []

        # Delegate to the core RepoMap instance, passing absolute paths
        return self.repomap.get_related_files(target_fnames=target_files_abs)


    def render_cache(self):
        """Renders all cached tags (for debugging)."""
        cache_path = Path(self.root) / TAGS_CACHE_DIR
        if not hasattr(self.repomap.TAGS_CACHE, 'iterkeys'):
             print("Cache is not a disk cache or does not support iteration.", file=sys.stderr)
             return ""
        if not cache_path.exists():
             print("Cache directory does not exist.", file=sys.stderr)
             return ""

        all_tags: List[TagInfo] = []
        all_cached_rel_fnames = set()
        cache = self.repomap.TAGS_CACHE # Use the instance's cache object

        try:
            num_keys = 0
            for key in cache.iterkeys(): # Key should be abs_fname
                num_keys += 1
                try:
                    abs_fname = key
                    # Basic check if path looks like a file path string
                    if not isinstance(abs_fname, str) or not os.path.isabs(abs_fname):
                         if self.verbose: print(f"Skipping invalid cache key: {key}", file=sys.stderr)
                         continue

                    # Check if file still exists before trying to get relpath
                    if not Path(abs_fname).is_file():
                         if self.verbose: print(f"Skipping cache for deleted file: {abs_fname}", file=sys.stderr)
                         continue

                    rel_fname = get_rel_fname(abs_fname, self.root)
                    all_cached_rel_fnames.add(rel_fname)

                    cached_item = cache.get(key)
                    # Check if item is dict and has 'data' which is a list (of tags)
                    if isinstance(cached_item, dict) and isinstance(cached_item.get("data"), list):
                        # Validate tag format before adding
                        valid_tags = [tag for tag in cached_item["data"] if isinstance(tag, tuple) and len(tag) == 5]
                        all_tags.extend(valid_tags)
                    elif self.verbose:
                        print(f"Invalid cache item format for key: {key}", file=sys.stderr)

                except ValueError as e: # Handle get_rel_fname errors
                    if self.verbose: print(f"Error getting relative path for cache key {key}: {e}", file=sys.stderr)
                except Exception as e:
                    print(f"Error reading cache item for key {key}: {e}", file=sys.stderr)

            if self.verbose: print(f"Checked {num_keys} cache keys.", file=sys.stderr)

            if not all_tags and not all_cached_rel_fnames:
                print("Cache appears empty or contains no valid tag data.", file=sys.stderr)
                return ""

            # Use a temporary RepoMap instance configured for rendering
            # Use existing tokenizer from the main repomap instance
            temp_mapper = RepoMap(
                root=self.root,
                map_tokens=1_000_000, # Unlimited tokens for dump
                verbose=self.verbose,
                tokenizer_name=self.repomap.tokenizer.name,
            )
            # Assign the collected tags to the temp mapper instance
            temp_mapper._all_tags = all_tags
            # Build definitions needed by to_tree from _all_tags
            temp_mapper._definitions = defaultdict(set)
            for tag_info in all_tags:
                 if tag_info[4] == 'def': # kind is index 4
                      temp_mapper._definitions[(tag_info[0], tag_info[3])].add(tag_info) # (rel_fname, name)

            # Get relative filenames that had tags
            tagged_rel_fnames = {tag[0] for tag in all_tags} # rel_fname is index 0
            # Include files that were cached but might not have yielded tags
            files_to_render_rel = sorted(list(all_cached_rel_fnames))

            # Prepare items for to_tree: Use actual tags where available, placeholders otherwise
            items_to_render: List[Union[TagInfo, FileNamePlaceholder]] = []
            rendered_files_set = set()

            # Add all tags first
            for tag in sorted(all_tags, key=lambda t: (t[0], t[2])): # Sort by filename, then line
                 items_to_render.append(tag)
                 rendered_files_set.add(tag[0]) # Add rel_fname

            # Add placeholders for cached files that didn't have tags rendered
            for rel_fname in files_to_render_rel:
                 if rel_fname not in rendered_files_set:
                      items_to_render.append((rel_fname,)) # Add placeholder

            # Sort primarily by filename derived from the item
            items_to_render.sort(key=lambda item: item[0])

            # Call to_tree on the temp mapper with the combined list
            return temp_mapper.to_tree(items_to_render)

        except Exception as e:
            print(f"Error rendering cache: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return ""


def main():
    """Command line interface for repomapper."""
    parser = argparse.ArgumentParser(description="Generate a repository map.")
    parser.add_argument("dir", help="Root directory of the repository.")
    parser.add_argument("--map-tokens", type=int, default=4096, help="Target token limit.")
    parser.add_argument("--map-mul-no-files", type=int, default=4, help="Multiplier for map tokens when no chat files.")
    parser.add_argument("--tokenizer", default="cl100k_base", help="Tiktoken tokenizer.")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore and overwrite cache.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output.")
    parser.add_argument("--output", help="Output file path (stdout if omitted).")
    parser.add_argument("--render-cache", action="store_true", help="Render cached tags instead of map.")
    parser.add_argument("--chat-files", nargs='*', default=[], help="Relative paths of files in chat context.")

    args = parser.parse_args()
    try:
        root_path = Path(args.dir).resolve() # Resolve root path once
        if not root_path.is_dir():
             raise FileNotFoundError(f"Root directory not found: {args.dir}")
    except Exception as e:
        print(f"Error resolving root directory '{args.dir}': {e}", file=sys.stderr)
        exit(1)

    try:
        mapper = RepoMapper(
            root_dir=str(root_path), # Pass resolved string path
            map_tokens=args.map_tokens,
            tokenizer=args.tokenizer,
            verbose=args.verbose,
            force_refresh=args.force_refresh,
            map_mul_no_files=args.map_mul_no_files, # Pass multiplier
        )

        content = ""
        if args.render_cache:
            print("Rendering cached data...", file=sys.stderr)
            content = mapper.render_cache()
            if not content:
                print("Cache rendering produced no output.", file=sys.stderr)
                # Don't exit, allow empty output to be written/printed
        else:
            # chat_files from args are relative paths, generate_map expects relative paths now
            valid_chat_files = []
            for f in args.chat_files:
                 p = root_path / f
                 # Check if it exists within the root directory before adding
                 if p.is_file() and str(p.resolve()).startswith(str(root_path)):
                     valid_chat_files.append(f) # Keep relative path
                 elif args.verbose:
                     print(f"Warning: Skipping chat file (not found or outside root): {f}", file=sys.stderr)

            print("Generating repository map...", file=sys.stderr)
            # Pass relative chat file paths to generate_map
            content = mapper.generate_map(chat_files=valid_chat_files)
            if not content:
                 print("Map generation produced no output.", file=sys.stderr)
                 # Don't exit, allow empty output to be written/printed

        # Output the result (either map or cache render)
        if args.output:
            try:
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True) # Ensure dir exists
                output_path.write_text(content, encoding="utf-8")
                if args.verbose: print(f"Output written to: {args.output}", file=sys.stderr)
            except IOError as e:
                print(f"Error writing output to {args.output}: {e}", file=sys.stderr, end="")
                print(". Falling back to stdout.")
                print(content) # Print to stdout on write error
        else:
            # Ensure consistent newline handling for stdout
            if content and not content.endswith('\n'):
                 print(content)
                 sys.stdout.flush() # Flush buffer
                 # Add the missing newline separately if needed, though print usually adds one
                 # print()
            elif content:
                 print(content, end='') # Print without adding extra newline if one exists
                 sys.stdout.flush()
            # else: print nothing if content is empty

    except (ImportError, ValueError, FileNotFoundError) as e:
         print(f"Error: {e}", file=sys.stderr)
         exit(1) # Exit on critical setup errors
    except Exception as e:
         print(f"An unexpected error occurred: {e}", file=sys.stderr)
         if args.verbose:
             import traceback
             traceback.print_exc(file=sys.stderr)
         exit(1) # Exit on unexpected errors


if __name__ == "__main__":
    main()
