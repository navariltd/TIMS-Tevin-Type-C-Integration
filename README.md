## TIMS Tevic Type-C Integration


### **Integrating TIMS Tevic Type C with ERPNext**

TIMS Tevic Type C app enhances business operations by ensuring compliance with Kenya Revenue Authority (KRA) regulations through real-time submission of invoices and credit notes. This integration streamlines tax reporting, improves operational efficiency, and ensures accurate financial records, reducing the risk of penalties while fostering trust with tax authorities.

----------

### **Requirements**

-   **Purchase the Type C Middleware for Accounting**  
    The middleware is essential for this integration and can be obtained from [Tevic](https://tevin.eu/type-c-middleware/).
    
-   **API Server Details**  
    Ensure you have the API server link and senderID provided by Tevic upon purchasing the middleware(Both fir sandbox and production).
    

----------

### **Configuration**

1.  **Setup TIMS Settings in ERPNext**  
    Navigate to the **TIMS Settings** doctype in ERPNext and fill out the following fields:
    
    -   **Company**: Select the company to associate with the integration.
    -   **Server Address**: Enter the API server address provided by Tevic.
    -   **Sender ID**: Enter the Sender ID from the API credentials.
    -   **Is Active**: Check this box to activate the integration.
    
    These details are critical for initializing the connection to the TIMS device.
    

----

### **Invoice Submission Process**

1.  **Automatic Submission to Tevic**
    
    -   When an invoice is **submitted** in ERPNext, the system automatically sends its details to Tevic.
    -   The payload (invoice data) and headers are prepared according to Tevic's requirements.
2.  **Integration Request Doctype**
    -   Upon submission, the transaction is recorded in the **Integration Request** doctype:
        -   **Status**: Initially set to "Queue."
        -   **Response**: If successful, the status changes to "Completed," and the response includes:
            -   A URL
            -   CU Number
        -   These details are stored on the respective invoice, and a QR code is generated.
        -   The QR code will appear on the invoice printout provided to the customer.
3.  **Credit Notes**
    
    -   The same process applies when submitting a credit note. The details are sent to Tevic, and the response is recorded.

----------

### **Error Handling**

1.  **Failed Transactions**
    
    -   If a transaction fails, check the **Integration Request** doctype:
        -   The **Error** field will display the reason for failure.
    -   Make the necessary changes and attempt resubmission.
2.  **Automatic Resubmission**
    
    -   A scheduled task runs every minute to resend pending invoices.
    -   Invoices that failed to send are automatically retried until successfully transmitted to Tevic.

----------

### **Print Format with QR Code**

Once an invoice is successfully submitted:

-   The CU Number and other response details are stored in the invoice.
-   A QR code is generated and embedded in the invoice printout.
-   The QR code serves as proof of compliance with KRA requirements and is presented to customers.

----------

### **Key Notes**

-   Ensure that all fields in **TIMS Settings** are correctly configured before submitting invoices.
-   Monitor the **Integration Request** doctype for any failed transactions and resolve errors promptly.
-   Keep the middleware and ERPNext updated to avoid integration issues.

----------

This setup ensures smooth and compliant submission of invoices and credit notes to Tevic, maintaining real-time communication with KRA.

#### License

agpl-3.0