import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import threading
import sys
import os

# Set appearance mode and default color theme
ctk.set_appearance_mode("System")  # Modes: "System" (standard), "Dark", "Light"
ctk.set_default_color_theme("blue")  # Themes: "blue" (standard), "green", "dark-blue"

class BaseFrame(ctk.CTkFrame):
    """Base frame for all frames in the application."""
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.master = master

class BaseApp(ctk.CTk):
    """Base application class for the LumiSync GUI."""
    def __init__(self):
        super().__init__()
        
        # Configure window
        self.title("LumiSync")
        self.geometry("900x600")
        self.minsize(800, 500)
        
        # Set up status bar first so it's always at the bottom
        self.setup_status_bar()

        # Create a container frame for the content that doesn't overlap with the status bar
        self.container = ctk.CTkFrame(self)
        self.container.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 0))

        # Dictionary to store frames
        self.frames = {}
        
        # Set up menu
        self.setup_menu()
        
        # Active threads
        self.active_threads = []

        # Schedule regular checks to ensure status bar visibility
        self.after(500, self.ensure_status_bar_visible)

    def setup_menu(self):
        """Set up the application menu."""
        # Create menu bar
        self.menu_bar = tk.Menu(self)
        
        # File menu
        file_menu = tk.Menu(self.menu_bar, tearoff=0)
        file_menu.add_command(label="Exit", command=self.on_closing)
        self.menu_bar.add_cascade(label="File", menu=file_menu)
        
        # Help menu
        help_menu = tk.Menu(self.menu_bar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about)
        self.menu_bar.add_cascade(label="Help", menu=help_menu)
        
        # Set the menu bar
        self.config(menu=self.menu_bar)
    
    def setup_status_bar(self):
        """Set up the status bar at the bottom of the window."""
        # Create a frame that will always be at the bottom
        self.status_bar = ctk.CTkFrame(self)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=5)

        # Add a visible border to make status bar more distinct
        self.status_bar.configure(border_width=1, border_color="#555555")

        # Status message label (left-aligned)
        self.status_label = ctk.CTkLabel(self.status_bar, text="Ready", anchor="w",
                                        font=("Segoe UI", 11))
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10, pady=5)

    def ensure_status_bar_visible(self):
        """Ensure the status bar is always visible."""
        # Get the required height for the status bar
        status_bar_height = self.status_bar.winfo_reqheight()

        # Get the current window dimensions
        window_height = self.winfo_height()
        window_width = self.winfo_width()

        # Define minimum window height to ensure status bar visibility
        # Add extra padding to make sure it's comfortably visible
        min_height = status_bar_height + 150

        # If window is too small, resize it
        if window_height < min_height:
            self.geometry(f"{window_width}x{min_height}")

        # Schedule next check - run frequently enough to catch resize events
        self.after(300, self.ensure_status_bar_visible)

    def set_status(self, message):
        """Set the status bar message."""
        self.status_label.configure(text=message)

        # Scroll to the status bar to ensure visibility
        self.update_idletasks()
        self.status_bar.update()

    def show_about(self):
        """Show the about dialog."""
        messagebox.showinfo(
            "About LumiSync",
            "LumiSync\n\nA program that allows you to easily sync your Govee lights.\n\n"
            "© 2023 Minlor"
        )
    
    def add_frame(self, frame_class, page_name):
        """Add a frame to the application."""
        frame = frame_class(self.container, self)
        self.frames[page_name] = frame
        frame.grid(row=0, column=0, sticky="nsew")
    
    def show_frame(self, page_name):
        """Show a frame for the given page name."""
        frame = self.frames[page_name]
        frame.tkraise()
    
    def run_in_thread(self, target, daemon=True, args=()):
        """Run a function in a separate thread."""
        thread = threading.Thread(target=target, daemon=daemon, args=args)
        thread.start()
        self.active_threads.append(thread)
        return thread
    
    def on_closing(self):
        """Handle window closing event."""
        if messagebox.askokcancel("Quit", "Do you want to quit?"):
            # Clean up any resources
            for thread in self.active_threads:
                if thread.is_alive():
                    # Can't forcibly terminate threads in Python, but we can set flags
                    # that the thread should check to know when to exit
                    pass
            
            self.destroy()
            sys.exit()
    
    def run(self):
        """Run the application."""
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.mainloop()