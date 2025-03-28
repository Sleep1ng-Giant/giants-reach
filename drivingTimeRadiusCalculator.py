import os
import pandas as pd
import numpy as np
import geopandas as gpd
import folium
from folium.plugins import HeatMap
import requests
import json
from shapely.geometry import Point
import tkinter as tk
from tkinter import ttk
from matplotlib.colors import LinearSegmentedColormap
import threading
import time
import math

class DrivingTimeRadiusCalculator:
    def __init__(self):
        self.api_key = None  # Will prompt user to enter API key
        self.zip_gdf = None
        self.state_boundaries = None
        self.results = None
        self.origin_address = None
        self.max_driving_time = None
        self.respect_state_lines = None
        self.origin_state = None
        
    def load_data(self):
        """Load zip code and state boundary data"""
        # Check if data already exists locally, otherwise download
        if not os.path.exists("zip_codes.geojson"):
            print("Downloading zip code data...")
            # Use Census Bureau's TIGER/Line shapefiles or similar open data
            # This is a simplified example - in a real app, you'd download the actual data
            url = "https://www2.census.gov/geo/tiger/TIGER2023/ZCTA520/tl_2023_us_zcta520.zip"
            # Code to download and extract the data would go here
            print("Please download zip code data from Census website and place in the same directory")
            return False
        
        print("Loading zip code boundaries...")
        self.zip_gdf = gpd.read_file("zip_codes.geojson")
        
        # # Load state boundaries
        # if not os.path.exists("states.geojson"):
        #     print("Please download state boundary data and place in the same directory")
        #     return False
        
        self.state_boundaries = None
        return True
    
    def geocode_address(self, address):
        """Convert address to coordinates and determine state"""
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={address}&key={self.api_key}"
        response = requests.get(url)
        data = response.json()
        
        if data['status'] != 'OK':
            print(f"Geocoding error: {data['status']}")
            return None, None
        
        location = data['results'][0]['geometry']['location']
        lat, lng = location['lat'], location['lng']
        
        # Find the state of the origin address
        state = None
        for component in data['results'][0]['address_components']:
            if 'administrative_area_level_1' in component['types']:
                state = component['short_name']
                break
        
        return (lat, lng), state
    
    def calculate_driving_times(self, origin_coords, sample_zips=None):
        """Calculate driving times from origin to zip code centroids"""
        if sample_zips is None:
            # If no sample is provided, use a subset for testing or process in batches
            # In a real implementation, you'd process in batches of ~100 destinations
            sample_zips = self.zip_gdf.sample(min(100, len(self.zip_gdf)))
        
        results = []
        batch_size = 25  # Google Maps API allows up to 25 destinations per request
        
        # Get origin coordinates as string
        origin = f"{origin_coords[0]},{origin_coords[1]}"
        
        # Process in batches
        for i in range(0, len(sample_zips), batch_size):
            batch = sample_zips.iloc[i:i+batch_size]
            
            # Get centroids of zip code polygons
            destinations = []
            for idx, row in batch.iterrows():
                centroid = row.geometry.centroid
                destinations.append(f"{centroid.y},{centroid.x}")
            
            destinations_str = "|".join(destinations)
            
            # Call Google Maps Routes API
            url = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": "originIndex,destinationIndex,duration"
            }
            origins_list = [{"location": {"latLng": {"latitude": origin_coords[0], "longitude": origin_coords[1]}}}]
            destinations_list = []

            for dest in destinations:
                lat, lng = dest.split(',')
                destinations_list.append({"location": {"latLng": {"latitude": float(lat), "longitude": float(lng)}}})

            payload = {
                "origins": origins_list,
                "destinations": destinations_list,
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_AWARE"
            }

            response = requests.post(url, json=payload, headers=headers)
            data = response.json()
            response = requests.get(url)
            data = response.json()
            
            if data['status'] != 'OK':
                print(f"API error: {data['status']}")
                continue
            
            # Process results
            if 'rows' in data:
                for element in data['rows']:
                    if 'elements' in element:
                        for j, route in enumerate(element['elements']):
                            if 'duration' in route:
                                zip_code = batch.iloc[j]['ZCTA5']  # Adjust field name
                                state = "VA"  # For Virginia only
                                driving_time = route['duration']['seconds'] / 60  # Convert seconds to minutes
                                
                                results.append({
                                    'zip_code': zip_code,
                                    'state': state,
                                    'driving_time_minutes': driving_time,
                                    'geometry': batch.iloc[j].geometry
                                })
    
    def filter_results(self, driving_results, max_time, respect_state_lines, origin_state):
        """Filter results based on max driving time and state constraints"""
        filtered = driving_results[driving_results['driving_time_minutes'] <= max_time]
        
        if respect_state_lines:
            filtered = filtered[filtered['state'] == origin_state]
        
        return filtered
    
    def create_map(self, filtered_results, origin_coords):
        """Create an interactive map showing the driving time radius"""
        # Create base map centered at origin
        m = folium.Map(location=[origin_coords[0], origin_coords[1]], zoom_start=10)
        
        # Add origin marker
        folium.Marker(
            location=[origin_coords[0], origin_coords[1]],
            popup="Origin",
            icon=folium.Icon(color="red", icon="home")
        ).add_to(m)
        
        # Create a GeoDataFrame from filtered results
        if len(filtered_results) > 0:
            gdf = gpd.GeoDataFrame(filtered_results)
            
            # Create a colormap based on driving times
            max_time = gdf['driving_time_minutes'].max()
            
            # Add polygons with colors based on driving time
            for idx, row in gdf.iterrows():
                color = self.get_color_for_time(row['driving_time_minutes'], max_time)
                
                folium.GeoJson(
                    row.geometry,
                    style_function=lambda x, color=color: {
                        'fillColor': color,
                        'color': 'black',
                        'weight': 1,
                        'fillOpacity': 0.6
                    },
                    tooltip=f"ZIP: {row['zip_code']}, Time: {row['driving_time_minutes']:.1f} min"
                ).add_to(m)
        
        # Save the map
        m.save('driving_time_radius.html')
        return 'driving_time_radius.html'
    
    def get_color_for_time(self, time, max_time):
        """Return a color based on the driving time (green to red)"""
        ratio = time / max_time
        r = min(255, int(255 * ratio))
        g = min(255, int(255 * (1 - ratio)))
        b = 0
        
        return f'#{r:02x}{g:02x}{b:02x}'
    
    def export_results(self, filtered_results, filename="zip_codes_in_range.csv"):
        """Export the filtered results to a CSV file"""
        if len(filtered_results) > 0:
            # Create a copy without the geometry column
            export_df = filtered_results.copy()
            export_df = export_df.drop(columns=['geometry'])
            export_df.to_csv(filename, index=False)
            return filename
        return None
    
    def run_calculation(self, address, max_time, respect_state):
        """Main method to run the full calculation process"""
        self.origin_address = address
        self.max_driving_time = max_time
        self.respect_state_lines = respect_state
        
        # Geocode the origin address
        origin_coords, self.origin_state = self.geocode_address(address)
        if not origin_coords:
            return "Geocoding failed. Please check the address."
        
        # Calculate driving times for all zip codes
        # In a real implementation, you'd use a smarter approach to limit API calls
        # such as expanding in concentric circles or using a pre-filtered dataset
        driving_results = self.calculate_driving_times(origin_coords)
        
        # Filter results based on criteria
        filtered_results = self.filter_results(
            driving_results, max_time, respect_state, self.origin_state
        )
        
        self.results = filtered_results
        
        # Create map
        map_file = self.create_map(filtered_results, origin_coords)
        
        # Export results
        csv_file = self.export_results(filtered_results)
        
        return f"Analysis complete. Found {len(filtered_results)} zip codes within {max_time} minutes."

# Create a simple GUI
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Driving Time Radius Calculator")
        self.root.geometry("600x400")
        
        self.calculator = DrivingTimeRadiusCalculator()
        
        # Create widgets
        frame = ttk.Frame(root, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)
        
        # API Key
        ttk.Label(frame, text="Google Maps API Key:").grid(column=0, row=0, sticky=tk.W, pady=5)
        self.api_key_var = tk.StringVar()
        api_key_entry = ttk.Entry(frame, width=40, textvariable=self.api_key_var)
        api_key_entry.grid(column=1, row=0, sticky=(tk.W, tk.E), padx=5, pady=5)
        
        # Address
        ttk.Label(frame, text="Origin Address:").grid(column=0, row=1, sticky=tk.W, pady=5)
        self.address_var = tk.StringVar()
        address_entry = ttk.Entry(frame, width=40, textvariable=self.address_var)
        address_entry.grid(column=1, row=1, sticky=(tk.W, tk.E), padx=5, pady=5)
        
        # Max driving time
        ttk.Label(frame, text="Max Driving Time (minutes):").grid(column=0, row=2, sticky=tk.W, pady=5)
        self.max_time_var = tk.IntVar(value=60)
        max_time_entry = ttk.Entry(frame, width=10, textvariable=self.max_time_var)
        max_time_entry.grid(column=1, row=2, sticky=tk.W, padx=5, pady=5)
        
        # Respect state lines
        self.respect_state_var = tk.BooleanVar(value=False)
        respect_state_check = ttk.Checkbutton(
            frame, text="Respect State Lines", variable=self.respect_state_var
        )
        respect_state_check.grid(column=1, row=3, sticky=tk.W, padx=5, pady=5)
        
        # Calculate button
        calculate_button = ttk.Button(frame, text="Calculate", command=self.calculate)
        calculate_button.grid(column=1, row=4, sticky=tk.E, padx=5, pady=20)
        
        # Status
        self.status_var = tk.StringVar(value="Ready")
        status_label = ttk.Label(frame, textvariable=self.status_var, wraplength=400)
        status_label.grid(column=0, row=5, columnspan=2, sticky=(tk.W, tk.E), pady=10)
        
        # Progress bar
        self.progress = ttk.Progressbar(frame, orient=tk.HORIZONTAL, length=100, mode='indeterminate')
        self.progress.grid(column=0, row=6, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        # Data loading button
        load_data_button = ttk.Button(frame, text="Load Data", command=self.load_data)
        load_data_button.grid(column=0, row=4, sticky=tk.W, padx=5, pady=20)
        
        # Configure grid
        for child in frame.winfo_children(): 
            child.grid_configure(padx=5, pady=5)
    
    def load_data(self):
        """Load zip code and state boundary data"""
        self.progress.start()
        self.status_var.set("Loading data...")
        
        def load_thread():
            success = self.calculator.load_data()
            if success:
                self.status_var.set("Data loaded successfully")
            else:
                self.status_var.set("Failed to load data. Please check the console for instructions.")
            self.progress.stop()
        
        thread = threading.Thread(target=load_thread)
        thread.daemon = True
        thread.start()
    
    def calculate(self):
        """Run the calculation process"""
        # Validate inputs
        if not self.api_key_var.get():
            self.status_var.set("Please enter your Google Maps API key")
            return
        
        if not self.address_var.get():
            self.status_var.set("Please enter an origin address")
            return
        
        if not self.calculator.zip_gdf is not None:
            self.status_var.set("Please load data first")
            return
        
        # Start progress bar
        self.progress.start()
        self.status_var.set("Calculating...")
        
        # Set API key
        self.calculator.api_key = self.api_key_var.get()
        
        def calculate_thread():
            result = self.calculator.run_calculation(
                self.address_var.get(),
                self.max_time_var.get(),
                self.respect_state_var.get()
            )
            
            self.status_var.set(result)
            self.progress.stop()
            
            # Open map in default browser
            if os.path.exists('driving_time_radius.html'):
                import webbrowser
                webbrowser.open('driving_time_radius.html')
        
        thread = threading.Thread(target=calculate_thread)
        thread.daemon = True
        thread.start()

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()