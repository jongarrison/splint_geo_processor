
curl -sS -i -H "Authorization: Bearer 5822765bec2c2bcba7343da70eed42bc5b4cbfc599483062b42433db77d190fa" http://splintserver.local:3000/api/geometry-processing/next-job

{"message":"No jobs available for processing"}

OR

{"id":"cmg6uzjh80001i8bwzuw27psq","GeometryID":"cmfrcxdbr0001i8dthofvn4a8","GeometryName":"Cylinder","GeometryAlgorithmName":"cylinder","GeometryInputParameterSchema":"[{\"InputName\":\"radius\",\"InputDescription\":\"Radius of Cylinder\",\"InputType\":\"Float\",\"NumberMin\":5,\"NumberMax\":100},{\"InputName\":\"height\",\"InputDescription\":\"Height of Cylinder\",\"InputType\":\"Float\",\"NumberMin\":10,\"NumberMax\":50}]","GeometryInputParameterData":"{\"radius\":5.1,\"height\":10.1}","CustomerNote":"Just a test 1","CustomerID":"Patient1","CreationTime":"2025-09-30T17:56:11.228Z","ProcessStartedTime":"2025-09-30T21:07:01.802Z","GeometryFileName":null,"PrintFileName":null,"creator":{"id":"user_admin_12345678","name":"Jon Garrison (Admin)","email":"jongarrison@gmail.com"},"owningOrganization":{"id":"org_default_12345678","name":"Default Organization"}}
