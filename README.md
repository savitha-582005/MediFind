# MediFind – Medicine Availability and Search System

MediFind is a web-based medicine search and pharmacy discovery system designed to help users find medicines and locate nearby pharmacies. The system provides a simple interface for customers to search for medicines and helps pharmacy owners manage their medicine inventory.

## Features

### 👤 Customer
- Search for medicines by name.
- Upload a medicine image for searching.
- Check medicine availability.
- Find nearby pharmacies based on location.
- View pharmacy details and available medicines.
- Get alternative medicine suggestions when the searched medicine is unavailable.

### 🏥 Pharmacy Owner
- Register and log in as a pharmacy owner.
- Add medicine details and availability.
- Update existing medicine information.
- Manage pharmacy inventory.
- Maintain pharmacy and medicine details.

### 📍 Location-Based Pharmacy Discovery
- Identifies nearby pharmacies based on the user's location.
- Sorts pharmacies according to their distance.
- Displays pharmacy locations using map visualization.

## Technology Stack

**Frontend**
- HTML
- CSS
- JavaScript

**Backend**
- Python
- Flask

**Database**
- MongoDB

## System Workflow

```text
User
  ↓
Select Customer / Owner
  ↓
Customer
  ├── Search Medicine
  ├── Upload Medicine Image
  ├── Check Availability
  └── Find Nearby Pharmacies
          ↓
     Pharmacy Details
          ↓
     Medicine Availability

Owner
  ├── Login / Register
  ├── Add Medicine
  ├── Update Medicine
  └── Manage Inventory
```

## Project Structure

```text
MediFind/
│
├── app.py
├── templates/
│   ├── index.html
│   ├── login.html
│   ├── register.html
│   ├── search.html
│   └── owner_dashboard.html
│
├── static/
│   ├── css/
│   ├── js/
│   └── images/
│
├── requirements.txt
└── README.md
```

> The exact file structure may vary depending on the final implementation.

## Installation and Setup

### 1. Clone the Repository

```bash
git clone <repository-url>
cd MediFind
```

### 2. Create a Virtual Environment

```bash
python -m venv venv
```

Activate the environment:

**Windows**

```bash
venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure MongoDB

Make sure MongoDB is installed and running.

Configure the MongoDB connection in the Flask application according to your project setup.

### 5. Run the Application

```bash
python app.py
```

Open the application in your browser:

```text
http://127.0.0.1:5000/
```

## Main Modules

### 1. Medicine Search
Allows customers to search for medicines using the medicine name or an uploaded medicine image.

### 2. Medicine Availability
Displays whether the requested medicine is available in registered pharmacies.

### 3. Pharmacy Discovery
Helps users discover nearby pharmacies using location-based sorting and map visualization.

### 4. Alternative Medicine Suggestions
If the searched medicine is unavailable, the system can suggest an alternative medicine where applicable.

### 5. Pharmacy Inventory Management
Pharmacy owners can add and update medicine information and manage their available inventory.

## Database

MediFind uses **MongoDB** to store application data such as:

- Customer information
- Pharmacy details
- Medicine information
- Medicine availability
- Owner information

## Future Enhancements

- Real-time medicine stock updates.
- Online medicine ordering.
- Prescription upload and verification.
- Pharmacy ratings and reviews.
- Notifications for medicine availability.
- Improved medicine image recognition.
- Secure online payment integration.

## Project Objective

The main objective of MediFind is to simplify the process of finding medicines and nearby pharmacies by providing a single platform for medicine search, availability checking, pharmacy discovery, and inventory management.

## Authors

**Savitha A.**  
B.E. – Artificial Intelligence & Machine Learning

## License

This project is developed for academic and educational purposes.
