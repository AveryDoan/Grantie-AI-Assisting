# My test applications

One sub-folder per applicant. Put that applicant's PDFs inside (name them like `01_Application_Responses.pdf`, `02_Confirmation_of_Enrolment.pdf`, `03_Flight_Booking_Itinerary.pdf`, `04_Letter_of_Support_1.pdf`, ...).

Start the demo API with a local snapshot (the snapshot keeps this application's data after restarts):

    APP_MODE=demo LLM_PROVIDER=stub DEMO_DATA_PATH=.local/grantie-demo.json uvicorn app.main:app --reload --port 8000

Load one folder once (API running):

    python -m seed.load_demo_applicant --dir my_tests/application_1 --api http://localhost:8000

The application remains in the queue at http://localhost:5173 after API restarts. Use synthetic data only.
