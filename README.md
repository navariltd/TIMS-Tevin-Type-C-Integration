## TIMS Tevin Type-C Integration


### **Integrating TIMS Tevin Type C with ERPNext**

TIMS Tevin Type C app enhances business operations by ensuring compliance with Kenya Revenue Authority (KRA) regulations through real-time submission of invoices and credit notes. This integration streamlines tax reporting, improves operational efficiency, and ensures accurate financial records, reducing the risk of penalties while fostering trust with tax authorities.

----------

### **Requirements**

-   **Purchase the Type C Middleware for Accounting**  
    The middleware is essential for this integration and can be obtained from [Tevin](https://tevin.eu/type-c-middleware/).
    ![Screenshot from 2024-12-13 12-26-37](https://github.com/user-attachments/assets/fdee247a-90ca-4b14-b028-834e83dfd35f)

-   **API Server Details**  
    Ensure you have the API server link and senderID provided by Tevin upon purchasing the middleware(Both for sandbox and production).
    

----------

### **Configuration**
![image](https://github.com/user-attachments/assets/d5c862c8-3ab8-46d0-b33a-51dc49725fc9)

1.  **Setup TIMS Settings in ERPNext**  
    Navigate to the **TIMS Settings** doctype in ERPNext and fill out the following fields:
    
    -   **Company**: Select the company to associate with the integration.
    -   **Server Address**: Enter the API server address provided by Tevin.
    -   **Sender ID**: Enter the Sender ID from the API credentials.
    -   **Is Active**: Check this box to activate the integration.
    
    These details are critical for initializing the connection to the TIMS device.
    

----

### **Invoice Submission Process**

1.  **Automatic Submission to Tevin**
    
    -   When an invoice is **submitted** in ERPNext, the system automatically sends its details to Tevin.
    -   The payload (invoice data) and headers are prepared according to Tevin's requirements.
      ![image (2)](https://github.com/user-attachments/assets/2e2e61fd-2f44-4481-a078-46f4f461248e)

    -   Successful response will create cu number and QR code on the invoice
2.  **Integration Request Doctype**
   ![image (1)](https://github.com/user-attachments/assets/fe3df703-b38e-4e1b-aa21-629f55b08d10)

    -   Upon submission, the transaction is recorded in the **Integration Request** doctype:
        -   **Status**: Initially set to "Queue."
        -   **Response**: If successful, the status changes to "Completed," and the response includes:
            -   A URL
            -   CU Number
        -   These details are stored on the respective invoice, and a QR code is generated.
        -   The QR code will appear on the invoice printout provided to the customer.
4.  **Credit Notes**
    
    -   The same process applies when submitting a credit note. The details are sent to Tevin, and the response is recorded.

----------

### **Error Handling**

1.  **Failed Transactions**
    
    -   If a transaction fails, check the **Integration Request** doctype:
        -   The **Error** field will display the reason for failure.
    -   Make the necessary changes and attempt resubmission.
2.  **Automatic Resubmission**
    
    -   A scheduled task runs every minute to resend pending invoices.
    -   Invoices that failed to send are automatically retried until successfully transmitted to Tevin.

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

This setup ensures smooth and compliant submission of invoices and credit notes to Tevin, maintaining real-time communication with KRA.

#### Manual/Self-Hosted Installation

1. [Install bench](https://github.com/frappe/bench)

  

2. [Install ERPNext](https://github.com/frappe/erpnext#installation)

    

3. Once bench and ERPNext are installed, add Tevin Type C app to your bench by running:

  
```sh


$  bench  get-app  --branch  {branch-name}  https://github.com/navariltd/TIMS-Tevin-Type-C-Integration.git

```


Replace `{branch-name}` with the desired branch name from the repository. Ensure compatibility with your installed versions of Frappe and ERPNext.


4. Install the tims-tevin-typec-integration app on your site by running:


```sh

$  bench  --site  {sitename}  install-app  tims_tevic_typec_integration

```

Replace `{sitename}` with the name of your site.

  

#### Frappe Cloud Installation

- Sign up with Frappe Cloud.

- Setup a [bench](https://frappecloud.com/docs/benches/create-new).

- Create a new site.

- Choose Frappe Version-15 or above, and select ERPNext, and TIMS Tevin Type-C Integration from the available Apps to Install.

- Within minutes, the site will be up and running with a fresh install, ready to explore the app's simple and impressive features.

  

If assistance is needed to get started, reach out for consultation and support from: [Navari](https://navari.co.ke/).

  

### Troubleshooting

- If you encounter any errors during installation, refer to the error messages for guidance.

- Ensure all dependencies are correctly installed and compatible with the versions specified.

#### License

agpl-3.0
