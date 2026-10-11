# My test applications

One sub-folder per applicant. Put that applicant's PDFs inside (name them like `01_Application_Responses.pdf`, `02_Confirmation_of_Enrolment.pdf`, `03_Flight_Booking_Itinerary.pdf`, `04_Letter_of_Support_1.pdf`, ...).

Load one folder (API running with `APP_MODE=demo LLM_PROVIDER=stub`):

    python -m seed.load_demo_applicant --dir my_tests/application_1

Each run creates a new application in the queue at http://localhost:5180. Use synthetic data only.
