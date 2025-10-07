;;; emigo-rpc.el --- RPC transport layer -*- lexical-binding: t; -*-

(require 'emigo-epc)
(require 'json)
(require 'emigo-actuator)

(defvar emigo--process nil
  "The Emigo agent process.")
(defvar emigo--connection nil
  "The EPC connection TO the Emigo agent (Emacs as client).")
(defvar emigo--rpc-server-manager nil
  "The EPC manager for the server that Python connects TO (Emacs as server).")

(defun emigo--first-start (port)
  "Connect to the python server on PORT and create a manager for the connection."
  (unless emigo--connection
    (let* ((conn (emigo-epc-connect "localhost" port))
           ;; Create a manager object to wrap the connection, which is
           ;; required by emigo-epc-call-sync and emigo-epc-manager-send.
           (manager (make-emigo-epc-manager :port port :connection conn)))
      (emigo-epc-init-epc-layer manager)
      (setq emigo--connection manager)
      (message "Emigo connected on port %d." port))))

(defun emigo--rpc-on-connect (manager)
  "Callback when Python agent connects to our EPC server."
  (message "Emigo agent has connected to Emacs EPC server.")
  ;; Generic
  (emigo-epc-define-method manager 'emigo-message #'message "Show MESSAGE in the echo area.")
  (emigo-epc-define-method manager 'emigo--first-start #'emigo--first-start "Handle first start signal from Python.")
  ;; Actuator
  (emigo-epc-define-method manager 'emigo-execute-intent #'emigo-execute-intent "Execute a semantic INTENT.")
  (emigo-epc-define-method manager 'emigo-undo-intent #'emigo-undo-intent "Undo an INTENT by ID.")
  ;; Indexer/Symbols
  (emigo-epc-define-method manager 'emigo-index-build #'emigo-index-build "Trigger a full rebuild of the symbol index.")
  (emigo-epc-define-method manager 'emacs_symbol_doc (lambda (s) (when-let ((sym (intern-soft s))) (documentation sym))) "Return the full docstring for a SYMBOL name.")
  (emigo-epc-define-method manager 'emacs_symbol_source
    (lambda (s)
      (when-let ((sym (intern-soft s)))
        (let ((file (ignore-errors (find-lisp-object-file sym))))
          (when file
            (let ((pos (ignore-errors (find-lisp-object-file sym file))))
              (list :file file
                    :line (when pos
                            (with-current-buffer (find-file-noselect file)
                              (line-number-at-pos pos)))))))))
    "Return a plist with :file and :line for a SYMBOL name.")
  (emigo-epc-define-method manager 'emacs_keybindings (lambda (s) (when-let ((sym (intern-soft s))) (let ((raw-keys (where-is-internal sym nil nil t))) (mapcar #'key-description raw-keys)))) "Return a list of keybindings for a SYMBOL name.")
  ;; Code/State Reading
  (emigo-epc-define-method manager 'emigo-read-sexps #'emigo-read-sexps "Read s-expression from target and return as string.")
  (emigo-epc-define-method manager 'emigo-list-buffers #'emigo-list-buffers "Return a list of buffer names.")
  (emigo-epc-define-method manager 'emigo-buffer-metadata #'emigo-buffer-metadata "Return metadata for a buffer.")
  (emigo-epc-define-method manager 'emigo-current-mode #'emigo-current-mode "Return the current major mode."))

(defun emigo--rpc-start-server ()
  "Start the Emacs-side EPC server for the agent to connect to.
Return the port number."
  ;; Based on legacy-emigo.el, emigo-epc-server-start returns a process.
  ;; The variable emigo--rpc-server-manager will hold this process.
  (unless (and emigo--rpc-server-manager (process-live-p emigo--rpc-server-manager))
    (setq emigo--rpc-server-manager (emigo-epc-server-start #'emigo--rpc-on-connect)))
  (unless (and emigo--rpc-server-manager (process-live-p emigo--rpc-server-manager))
    (error "[Emigo] Failed to start Emacs EPC server."))
  (process-contact emigo--rpc-server-manager :service))

(defun emigo--rpc-process-filter (proc string)
  "Filter for the agent process output."
  (with-current-buffer (process-buffer proc)
    (insert string)))

(defun emigo--rpc-process-sentinel (proc msg)
  "Sentinel for the agent process."
  (setq emigo--process nil)
  (setq emigo--connection nil)
  (message "Emigo agent process died: %s" msg))

(defun emigo-rpc-connect ()
  "Start the Python agent process and connect to it."
  (when emigo--process
    (user-error "Emigo agent process is already running."))
  (let* ((emacs-server-port (emigo--rpc-start-server))
         (root-dir (file-name-directory (file-name-directory (locate-library "emigo-rpc.el"))))
         (proc-buffer (get-buffer-create "*Emigo Process*"))
         ;; Run python from the project root so module imports work.
         (default-directory root-dir))
    (setq emigo--process
          (start-process "emigo-agent"
                         proc-buffer
                         "python3"
                         "-m" "python.bridge.server"
                         (number-to-string emacs-server-port)))
    (set-process-filter emigo--process #'emigo--rpc-process-filter)
    (set-process-sentinel emigo--process #'emigo--rpc-process-sentinel)))

(defun emigo-rpc-call (method &rest params)
  "Send a synchronous RPC call to the Python agent."
  (unless emigo--connection
    (user-error "Emigo agent not connected."))
  (let* ((request `((id . ,(emigo-epc-uid))
                    (type . "call")
                    (method . ,method)
                    (params . ,(apply #'list params))))
         ;; The remote method `handle_request` takes one argument, the JSON string.
         (response (emigo-epc-call-sync emigo--connection 'handle_request (json-serialize request))))
    ;; The response is a plist converted from the Python dictionary by EPC.
    (if (plist-get response :ok)
        (plist-get response :result)
      (let ((error (plist-get response :error)))
        (user-error "Emigo RPC Error: %s (code: %s)"
                    (plist-get error :message)
                    (plist-get error :code))))))

(defun emigo-rpc-notify (method &rest params)
  "Send an asynchronous RPC notification to the Python agent."
  (when emigo--connection
    (let* ((request-json (json-serialize
                          `(:id ,(emigo-epc-uid)
                            :type "call"
                            :method ,method
                            :params ,(vconcat params))))  ; vconcat creates a vector from the list
           (args-list (list request-json)))
      (emigo-epc-call-deferred emigo--connection 'handle_request args-list))))

(provide 'emigo-rpc)

;;; emigo-rpc.el ends here
